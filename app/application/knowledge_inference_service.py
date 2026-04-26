"""v1.2.0 — shared service for "use AI to populate an offer's KB".

Pre-v1.2.0 the AI knowledge-inference flow lived inside the WebUI-
facing endpoints in ``app/api/ai.py:127-200`` (``/ai/infer-offer-
knowledge`` + ``/ai/infer-offer-knowledge-stream``). MCP and CLI had
no access — they couldn't reuse the logic without HTTP-recursing into
the same process. Result: WebUI offers got rich KB rows from the
inference; MCP/CLI offers had only what the agent manually typed,
which never matched the discipline of the 167-line system prompt at
``app/adapters/ai.py:61-227``.

This service centralises the workflow:

1. Load the target offer and its existing KB rows
2. Build the offer_data dict the adapter expects
3. Call ``OpenAICompatibleAdapter.infer_knowledge`` — same adapter,
   same prompt, same model selection (scene_key="knowledge"). No
   second copy of the prompt, no risk of drift.
4. Persist the returned suggestions as ``knowledge_items`` rows with
   ``source_type=ai_inferred`` provenance
5. Return a structured report that MCP / REST / CLI can surface

The WebUI endpoints in ``app/api/ai.py`` continue to work — they're
just thin wrappers over this service now (see Step 3 in the v1.2.0
plan). The streaming path stays in the endpoint because SSE is an
HTTP-transport concern; the non-streaming path delegates here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import OpenAICompatibleAdapter, StubAIAdapter, get_ai_adapter
from app.exceptions import NotFoundError
from app.infrastructure.knowledge_repo import KnowledgeItemRepository
from app.infrastructure.offer_repo import OfferRepository
from app.schemas.knowledge_inference import KnowledgeInferenceReport

logger = logging.getLogger(__name__)


def build_offer_data(
    *,
    offer_name: str,
    offer_type: str,
    description: str | None,
    existing_knowledge: list[dict[str, Any]] | None = None,
) -> dict:
    """Construct the input dict that ``adapter.infer_knowledge`` expects.

    Mirrors ``app/api/ai.py:_build_offer_data``'s shape exactly so the
    LLM sees the same payload regardless of which entry point
    triggered the inference. Two endpoints, one shape — no per-caller
    drift in what the model sees.
    """
    return {
        "offer": {
            "name": offer_name,
            "offer_type": offer_type,
            "description": description or "",
        },
        # These three keep their slots so the adapter's prompt-builder
        # template stays uniform; the prompt itself reads
        # ``existing_knowledge`` for the recall context.
        "selling_points": [],
        "target_audiences": [],
        "target_scenarios": [],
        "knowledge_items": existing_knowledge or [],
    }


def friendly_llm_error(e: Exception, adapter) -> str:
    """Turn an OpenAI SDK exception into a user-actionable message.

    Lifted from ``app/api/ai.py:_friendly_llm_error`` so MCP / CLI
    callers get the same Chinese-localised hints the WebUI shows
    (model label + class of failure + where to go next). The
    endpoint version is now a 1-liner that delegates here.
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        RateLimitError,
    )

    provider = getattr(adapter, "provider", "?")
    model = getattr(adapter, "model", "?")
    label = f"{provider}/{model}"

    if isinstance(e, APITimeoutError):
        return (
            f"模型 {label} 连续超时 3 次未响应。"
            f"请检查：(1) Settings → 模型配置中该 LLM 的 API 地址是否可达；"
            f"(2) 若走代理需确认代理服务正常；(3) 可在 Settings 中将 knowledge 场景切换到其他可用模型。"
        )
    if isinstance(e, APIConnectionError):
        return (
            f"模型 {label} 连接失败：{e}。"
            f"请检查 Settings → 模型配置中的 base_url 与网络连通性；或切换到其他模型。"
        )
    if isinstance(e, RateLimitError):
        return (
            f"模型 {label} 触发限流 / 额度耗尽：{e}。"
            f"请补充该 LLM 账户额度，或在 Settings 中切换到其他可用模型。"
        )
    if isinstance(e, AuthenticationError):
        return (
            f"模型 {label} 鉴权失败：{e}。"
            f"请在 Settings → 模型配置中核对 API Key。"
        )
    if isinstance(e, BadRequestError):
        return f"模型 {label} 拒绝请求：{e}。可能是 prompt 超长或参数不兼容，请反馈。"
    return f"模型 {label} 调用失败：{e}"


_KNOWLEDGE_TYPES = (
    "selling_point", "audience", "scenario",
    "pain_point", "faq", "objection", "proof",
)


class KnowledgeInferenceService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.offer_repo = OfferRepository(session)
        self.kb_repo = KnowledgeItemRepository(session)

    async def infer_and_persist_offer_knowledge(
        self,
        offer_id: uuid.UUID,
        *,
        language: str | None = None,
        trigger: str = "manual:rest_endpoint",
        user_hint: str | None = None,
    ) -> KnowledgeInferenceReport:
        """Run AI inference for an offer + persist returned suggestions.

        ``trigger`` is recorded in ``source_ref`` so an audit can
        distinguish rows from ``create_offer(infer_knowledge=True)``
        vs. a manual ``infer_knowledge_for_offer`` re-run vs. a REST
        endpoint hit. Format: ``auto-infer:<trigger>:<offer_id>``.

        On adapter failure the offer's KB is left untouched and the
        report carries ``success=False`` with the friendly error
        message — callers (MCP, REST, CLI) decide whether to retry or
        surface the message. We never raise out of this method when
        the failure is the LLM's; that would force every caller to
        wrap it, and worse, would prevent ``create_offer`` from
        succeeding when the offer creation itself worked fine.
        """
        offer = await self.offer_repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundError("Offer", str(offer_id))

        # Effective language: explicit > offer.locale > zh-CN.
        # Mirrors the endpoint's intent of "follow content over UI"
        # but exposes language explicitly so MCP/CLI callers can
        # override when they know the source-content language.
        lang = language or getattr(offer, "locale", None) or "zh-CN"

        existing_items, _ = await self.kb_repo.list(
            scope_type="offer", scope_id=offer_id, offset=0, limit=500,
        )
        existing_payload = [
            {
                "knowledge_type": ki.knowledge_type,
                "title": ki.title,
                "content_raw": ki.content_raw or "",
            }
            for ki in existing_items
        ]

        offer_data = build_offer_data(
            offer_name=offer.name,
            offer_type=offer.offer_type,
            description=offer.description,
            existing_knowledge=existing_payload,
        )

        adapter = await get_ai_adapter(self.session, scene_key="knowledge")
        if isinstance(adapter, StubAIAdapter):
            return KnowledgeInferenceReport(
                success=False, offer_id=offer_id,
                reason="NO_LLM_CONFIGURED — please configure a knowledge-scene model in Settings.",
            )
        if not isinstance(adapter, OpenAICompatibleAdapter):
            return KnowledgeInferenceReport(
                success=False, offer_id=offer_id,
                reason=f"Adapter type {type(adapter).__name__} does not support infer_knowledge.",
            )

        try:
            raw = await adapter.infer_knowledge(offer_data, lang, user_hint=user_hint)
        except Exception as e:  # pragma: no cover — exercised in MCP integration test
            logger.error(
                "infer_and_persist_offer_knowledge | adapter call failed | offer=%s lang=%s | %s",
                offer_id, lang, e, exc_info=True,
            )
            return KnowledgeInferenceReport(
                success=False, offer_id=offer_id,
                reason=friendly_llm_error(e, adapter),
                model_label=f"{adapter.provider}/{adapter.model}",
            )

        if not isinstance(raw, dict) or not any(raw.get(k) for k in _KNOWLEDGE_TYPES):
            # Empty / malformed response. The offer creation succeeded
            # (or the manual trigger ran) — we just have nothing to
            # write. Treat as success with zero counts so the caller
            # can branch on ``written_count == 0`` if they want to
            # retry, vs. on ``success=False`` for hard errors.
            return KnowledgeInferenceReport(
                success=True, offer_id=offer_id,
                written_count=0, updated_count=0, by_type={},
                model_label=f"{adapter.provider}/{adapter.model}",
            )

        # Persist. The ``(scope_type, scope_id, knowledge_type, title)``
        # unique constraint (``uq_knowledge_title``) lets us upsert the
        # whole batch in one PG ``INSERT ... ON CONFLICT DO UPDATE`` —
        # was N×2 sequential round-trips per item (find_by_title + then
        # update/create), now a single statement. ``confidence`` is
        # ``COALESCE(EXCLUDED.confidence, current)``-merged in the repo
        # helper so an LLM run that returns no score preserves the
        # prior one.
        source_ref = f"auto-infer:{trigger}:{offer_id}"
        rows: list[dict] = []
        by_type: dict[str, int] = {}

        for kt in _KNOWLEDGE_TYPES:
            for item in raw.get(kt) or []:
                if not isinstance(item, dict):
                    continue
                title = (item.get("title") or "").strip()
                if not title:
                    continue  # malformed — skip

                confidence = item.get("confidence")
                try:
                    confidence = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    confidence = None

                rows.append({
                    "scope_type": "offer",
                    "scope_id": offer_id,
                    "knowledge_type": kt,
                    "title": title,
                    "content_raw": item.get("content_raw") or "",
                    "source_type": "ai_inferred",
                    "source_ref": source_ref,
                    "language": lang,
                    "confidence": confidence,
                })
                by_type[kt] = by_type.get(kt, 0) + 1

        written, updated, _ids = await self.kb_repo.upsert_many_full(rows)

        return KnowledgeInferenceReport(
            success=True, offer_id=offer_id,
            written_count=written, updated_count=updated, by_type=by_type,
            model_label=f"{adapter.provider}/{adapter.model}",
        )
