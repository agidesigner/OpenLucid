from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import AIAdapter, _extract_thinking, get_ai_adapter
from app.application.context_service import ContextService
from app.apps.kb_qa_styles import DEFAULT_STYLE_ID, STYLE_TEMPLATES
from app.schemas.app import KBQAAskRequest, KBQAAskResponse, KBQAReferencedKnowledge

logger = logging.getLogger(__name__)

# Limits to keep prompt compact and LLM fast
_MAX_KNOWLEDGE_ITEMS = 15
_MAX_CONTENT_CHARS = 500


def _tokenize(text: str) -> list[str]:
    """Split text into overlapping bigrams + unigrams for matching.

    Works for both Chinese (character bigrams) and English (word-level).
    """
    import re
    # Split on whitespace and punctuation, keep CJK chars as individual tokens
    raw = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", text.lower())
    tokens = list(raw)
    # Add bigrams for better phrase matching
    for i in range(len(raw) - 1):
        tokens.append(raw[i] + raw[i + 1])
    return tokens


def _rank_knowledge(question: str, items: list[dict]) -> list[dict]:
    """Relevance ranking using word-level matching with title boost and type affinity.

    Scoring strategy:
    - Word/bigram overlap between question and item text (title + content)
    - Title matches weighted 3x (titles are concise and high-signal)
    - FAQ/objection items boosted when question contains question patterns
    - Items with zero overlap pushed to the bottom
    """
    q_tokens = set(_tokenize(question))
    if not q_tokens:
        return items

    # Detect question intent — boost FAQ/objection types
    is_question = any(kw in question for kw in ("？", "?", "吗", "呢", "为什么", "怎么", "如何", "什么", "how", "why", "what"))

    def _score(item: dict) -> float:
        title = item.get("title", "")
        content = item.get("content_raw", "")
        ktype = item.get("knowledge_type", "general")

        title_tokens = set(_tokenize(title))
        content_tokens = set(_tokenize(content))

        # Title overlap weighted 3x
        title_overlap = len(q_tokens & title_tokens)
        content_overlap = len(q_tokens & content_tokens)
        score = (title_overlap * 3.0 + content_overlap) / max(len(q_tokens), 1)

        # Type affinity boost
        if is_question and ktype in ("faq", "objection"):
            score *= 1.5

        return score

    return sorted(items, key=_score, reverse=True)


class KBQAService:
    def __init__(self, session: AsyncSession, ai_adapter: AIAdapter | None = None):
        self.session = session
        self.ai = ai_adapter

    async def _prepare(self, request: KBQAAskRequest):
        """Resolve adapter, load context, rank knowledge, build prompt.
        Returns (adapter, knowledge_items, all_items, style_prompt, context).
        """
        if not self.ai:
            self.ai = await get_ai_adapter(
                self.session,
                scene_key="kb_qa",
                config_id=request.config_id,
                model_override=request.model_override,
            )

        logger.info("KB QA: using adapter %s/%s for offer %s",
                     getattr(self.ai, 'provider', '?'), getattr(self.ai, 'model', '?'), request.offer_id)

        ctx_service = ContextService(self.session)
        context = await ctx_service.get_offer_context(request.offer_id)

        all_items = []
        for k in context.knowledge_items:
            content = (k.content_raw or "")[:_MAX_CONTENT_CHARS]
            all_items.append({
                "id": str(k.id),
                "knowledge_type": k.knowledge_type,
                "title": k.title,
                "content_raw": content,
            })

        ranked = _rank_knowledge(request.question, all_items)
        knowledge_items = ranked[:_MAX_KNOWLEDGE_ITEMS]

        # Presence-of-language rule: explicit API value wins, else KB.
        from app.libs.lang_detect import resolve_output_language
        kb_sample = " ".join((k.get("title") or "") + " " + (k.get("content_raw") or "") for k in knowledge_items)
        request.language = resolve_output_language(
            request.language, kb_sample, caller="kb_qa",
        )

        type_counts = {}
        for k in knowledge_items:
            t = k.get("knowledge_type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        logger.info("KB QA: loaded %d/%d knowledge items (ranked top %d) %s",
                     len(knowledge_items), len(all_items), _MAX_KNOWLEDGE_ITEMS, type_counts)

        style = STYLE_TEMPLATES.get(request.style_id) or STYLE_TEMPLATES[DEFAULT_STYLE_ID]
        logger.info("KB QA: style=%s, lang=%s, question=\"%s\"", request.style_id, request.language, request.question[:80])

        brand_voice = await ctx_service.resolve_brand_voice(request.offer_id)
        return self.ai, knowledge_items, all_items, style.system_prompt_prefix, context, brand_voice

    async def ask(self, request: KBQAAskRequest) -> KBQAAskResponse:
        t0 = time.monotonic()
        adapter, knowledge_items, all_items, style_prompt, context, brand_voice = await self._prepare(request)

        result = await adapter.answer_from_knowledge(
            question=request.question,
            knowledge_items=knowledge_items,
            style_prompt=style_prompt,
            language=request.language,
            brand_voice=brand_voice,
        )

        elapsed = time.monotonic() - t0
        logger.info("KB QA: answer length=%d, referenced=%d items, has_relevant=%s, elapsed=%.1fs",
                     len(result.get("answer", "")), len(result.get("referenced_titles", [])),
                     result.get("has_relevant_knowledge", False), elapsed)

        referenced = []
        title_to_item = {k.title: k for k in context.knowledge_items}
        for title in result.get("referenced_titles", []):
            item = title_to_item.get(title)
            referenced.append(KBQAReferencedKnowledge(
                knowledge_id=item.id if item else None,
                title=title,
                knowledge_type=item.knowledge_type if item else "unknown",
            ))

        return KBQAAskResponse(
            answer=result.get("answer", ""),
            style_id=request.style_id,
            referenced_knowledge=referenced,
            knowledge_count=len(all_items),
            has_relevant_knowledge=result.get("has_relevant_knowledge", False),
            thinking=result.get("thinking"),
        )

    async def ask_stream(self, request: KBQAAskRequest) -> AsyncIterator[str]:
        """Yield SSE events: thinking tokens, then a final result JSON."""
        from app.adapters.ai import OpenAICompatibleAdapter

        t0 = time.monotonic()
        adapter, knowledge_items, all_items, style_prompt, context, brand_voice = await self._prepare(request)

        # Non-streaming adapters (StubAIAdapter) fall back to non-stream
        if not isinstance(adapter, OpenAICompatibleAdapter):
            result = await adapter.answer_from_knowledge(
                question=request.question,
                knowledge_items=knowledge_items,
                style_prompt=style_prompt,
                language=request.language,
                brand_voice=brand_voice,
            )
            yield f"event: result\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
            return

        from app.adapters.prompt_builder import format_brand_voice_layer
        system = adapter._build_kb_qa_prompt(knowledge_items, style_prompt, language=request.language)
        system += format_brand_voice_layer(brand_voice, request.language)

        # Stream tokens from LLM, detecting <think>...</think> boundaries.
        # Wrap the LLM iteration in try/except so any failure (timeout,
        # connect error, rate limit, insufficient balance) becomes a
        # terminal SSE error event the frontend can render. Without
        # this, an exception drops the stream silently and the user
        # sees a stuck spinner with no explanation. Mirror the pattern
        # used by topic_studio + script_writer streaming endpoints.
        full_output = ""
        state = "before_think"  # before_think → in_think → after_think (or no_think)

        try:
            async for token in adapter._chat_stream(system, request.question, temperature=0.3):
                full_output += token

                if state == "before_think":
                    if "<think>" in full_output:
                        state = "in_think"
                        after_tag = full_output.split("<think>", 1)[1]
                        if after_tag:
                            yield f"event: thinking\ndata: {json.dumps(after_tag, ensure_ascii=False)}\n\n"
                    elif len(full_output) > 20 and "<" not in full_output:
                        state = "no_think"

                elif state == "in_think":
                    if "</think>" in full_output:
                        before_close = token.split("</think>")[0]
                        if before_close:
                            yield f"event: thinking\ndata: {json.dumps(before_close, ensure_ascii=False)}\n\n"
                        state = "after_think"
                        yield "event: thinking_done\ndata: {}\n\n"
                    else:
                        yield f"event: thinking\ndata: {json.dumps(token, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("KB QA stream: LLM call failed")
            detail = str(e)
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    body = resp.json()
                    err = body.get("error")
                    if isinstance(err, dict) and err.get("message"):
                        detail = err["message"]
                    else:
                        detail = body.get("message") or body.get("detail") or detail
                except Exception:
                    pass
            yield f"event: error\ndata: {json.dumps({'message': detail[:500]}, ensure_ascii=False)}\n\n"
            return

        elapsed = time.monotonic() - t0

        # Parse the final output
        thinking, clean_result = _extract_thinking(full_output)
        if state == "in_think":
            # Think never closed (malformed) — close it now
            yield "event: thinking_done\ndata: {}\n\n"

        logger.info("KB QA stream: LLM responded in %.1fs, thinking=%d chars", elapsed, len(thinking))

        try:
            parsed = adapter._parse_json_response(clean_result)
        except (json.JSONDecodeError, ValueError):
            logger.error("Failed to parse KB QA stream response: %s", clean_result[:500])
            parsed = {"answer": clean_result, "referenced_titles": [], "has_relevant_knowledge": bool(knowledge_items)}

        # Build final result with referenced knowledge mapping
        referenced = []
        title_to_item = {k.title: k for k in context.knowledge_items}
        for title in parsed.get("referenced_titles", []):
            item = title_to_item.get(title)
            referenced.append({
                "knowledge_id": str(item.id) if item else None,
                "title": title,
                "knowledge_type": item.knowledge_type if item else "unknown",
            })

        result = {
            "answer": parsed.get("answer", clean_result),
            "style_id": request.style_id,
            "referenced_knowledge": referenced,
            "knowledge_count": len(all_items),
            "has_relevant_knowledge": parsed.get("has_relevant_knowledge", bool(knowledge_items)),
        }

        logger.info("KB QA stream: answer length=%d, referenced=%d items, elapsed=%.1fs",
                     len(result["answer"]), len(referenced), elapsed)

        yield f"event: result\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
