from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError, NotFoundError
from app.infrastructure.creation_repo import CreationRepository
from app.models.creation import Creation
from app.models.merchant import Merchant
from app.models.offer import Offer
from app.models.video_generation_job import VideoGenerationJob
from app.schemas.creation import CreationCreate, CreationUpdate

logger = logging.getLogger(__name__)


class CreationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CreationRepository(session)

    async def create(self, data: CreationCreate) -> Creation:
        # Resolve merchant_id: prefer offer_id derivation, then explicit, then fallback
        merchant_id = data.merchant_id
        offer_id = data.offer_id

        if offer_id and not merchant_id:
            offer = await self.session.get(Offer, offer_id)
            if not offer:
                raise NotFoundError("Offer", str(offer_id))
            merchant_id = offer.merchant_id

        if not merchant_id:
            # Single-merchant fallback (most self-hosted users have one merchant)
            result = await self.session.execute(
                select(Merchant.id).order_by(Merchant.created_at).limit(2)
            )
            ids = [row[0] for row in result]
            if len(ids) == 1:
                merchant_id = ids[0]
            elif len(ids) > 1:
                raise AppError(
                    code="MERCHANT_REQUIRED",
                    message="merchant_id is required when multiple merchants exist; or pass an offer_id and it will be derived from there",
                    status_code=400,
                )
            else:
                raise AppError(
                    code="NO_MERCHANT",
                    message="No merchant found — create one first",
                    status_code=400,
                )

        creation = await self.repo.create(
            merchant_id=merchant_id,
            offer_id=offer_id,
            title=data.title.strip(),
            content=data.content,
            content_type=data.content_type or "general",
            tags=data.tags or None,
            source_app=data.source_app or "manual",
            source_note=data.source_note,
        )
        logger.info(
            "Creation saved: id=%s title=%r source=%s offer=%s",
            creation.id, creation.title[:60], creation.source_app, offer_id,
        )
        return creation

    async def get(self, creation_id: uuid.UUID) -> Creation:
        creation = await self.repo.get_by_id(creation_id)
        if not creation:
            raise NotFoundError("Creation", str(creation_id))
        return creation

    async def list(
        self,
        merchant_id: uuid.UUID | None = None,
        offer_id: uuid.UUID | None = None,
        content_type: str | None = None,
        source_app: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Creation], int]:
        offset = (page - 1) * page_size
        return await self.repo.list(
            merchant_id=merchant_id,
            offer_id=offer_id,
            content_type=content_type,
            source_app=source_app,
            q=q,
            offset=offset,
            limit=page_size,
        )

    async def get_video_summaries(
        self, creation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict]:
        """Bulk-fetch video count + latest video per creation.

        Returns: { creation_id: {"count": int, "latest": {status, cover_url, video_url} | None} }

        One query, ordered DESC so the first job seen per creation is the latest.
        """
        if not creation_ids:
            return {}
        result = await self.session.execute(
            select(VideoGenerationJob)
            .where(VideoGenerationJob.creation_id.in_(creation_ids))
            .order_by(
                VideoGenerationJob.creation_id,
                VideoGenerationJob.created_at.desc(),
            )
        )
        jobs = result.scalars().all()
        summary: dict[uuid.UUID, dict] = {}
        for j in jobs:
            cid = j.creation_id
            if cid not in summary:
                summary[cid] = {
                    "count": 0,
                    "latest": {
                        "status": j.status,
                        "cover_url": j.cover_url,
                        "video_url": j.video_url,
                    },
                }
            summary[cid]["count"] += 1
        return summary

    async def update(self, creation_id: uuid.UUID, data: CreationUpdate) -> Creation:
        creation = await self.repo.update(
            creation_id,
            title=data.title.strip() if data.title else None,
            content=data.content,
            content_type=data.content_type,
            tags=data.tags,
            source_note=data.source_note,
        )
        if not creation:
            raise NotFoundError("Creation", str(creation_id))
        return creation

    async def delete(self, creation_id: uuid.UUID) -> None:
        ok = await self.repo.delete(creation_id)
        if not ok:
            raise NotFoundError("Creation", str(creation_id))

    async def regenerate_broll_plan(self, creation_id: uuid.UUID) -> Creation:
        """Ask the LLM to propose a new ``broll_plan`` for this creation's
        existing script. Re-uses the composer's shot-design rules so the new
        plan follows the same distribution discipline. Persists the sanitized
        result onto ``structured_content.broll_plan``.

        Script text / structure_id / platform_id stay unchanged — only the
        broll_plan array is replaced. Users hit this when the current plan
        doesn't match their taste and they'd rather not rewrite the script.
        """
        import json
        from app.adapters.ai import OpenAICompatibleAdapter, get_ai_adapter
        from app.application.script_writer_service import _sanitize_broll_plan

        creation = await self.get(creation_id)
        sc = dict(creation.structured_content or {})
        sections = sc.get("sections") or {}
        section_ids = sc.get("section_ids") or list(sections.keys())
        if not sections or not section_ids:
            raise AppError("This creation has no script sections to plan B-roll over")

        narration = "".join((sections.get(sid) or {}).get("text", "") for sid in section_ids)
        total_chars = len(narration)
        est_seconds = sc.get("estimated_total_seconds") or max(20, int(total_chars / 5))

        # Target shot count per composer rules
        if est_seconds < 30:
            target_count = 2
        elif est_seconds < 60:
            target_count = 3
        elif est_seconds < 90:
            target_count = 4
        else:
            target_count = 5

        from app.libs.lang_detect import detect_text_language
        detected = detect_text_language(narration[:2000])
        is_en = (detected == "en")

        if is_en:
            system = (
                "You are an AI director. Given a script, propose a B-roll plan that "
                "keeps the 8-12 second visual-change cadence. Output STRICT JSON: "
                "a single array where each item has these fields:\n"
                "  - type: \"retention\" (first item, position 0, 5s opener) "
                "or \"illustrative\" (5-7s cutaways)\n"
                "  - insert_after_char: INTEGER character offset in the concatenated narration (0 to total length)\n"
                "  - duration_seconds: INTEGER 5-10\n"
                "  - prompt: concrete scene description for AI image/video gen (avoid faces)\n"
                f"Total narration length: {total_chars} characters. "
                f"Estimated video duration: {est_seconds} seconds. "
                f"Target shot count: {target_count} (1 retention + {target_count-1} illustrative). "
                "Adjacent illustrative insert_after_char values must differ by no more than 60 chars. "
                "DO NOT place B-roll inside the final CTA section. "
                "Output JSON array only — no prose, no code fences."
            )
            user = f"Narration:\n{narration}"
        else:
            system = (
                "你是 AI 编导。根据下方口播文案，规划一套 B-roll 分镜，保持 8-12 秒一次视觉变化的节奏。"
                "输出严格 JSON：一个数组，每项包含：\n"
                "  - type：首项必须 \"retention\"（位置 0，5 秒开场留人），其余为 \"illustrative\"（5-7 秒插入）\n"
                "  - insert_after_char：整数字符位（0 到口播总字数之间），"
                "不得是中文句子或描述性短语\n"
                "  - duration_seconds：整数 5-10\n"
                "  - prompt：具体的 AI 可生成画面场景描述（避免人脸）\n"
                f"口播总字数：{total_chars}。预估视频时长：{est_seconds} 秒。目标分镜数：{target_count}"
                f"（1 retention + {target_count-1} illustrative）。"
                "相邻 illustrative 的 insert_after_char 差不得超过 60。"
                "最后 CTA 段不要放 B-roll。只输出 JSON 数组，不加任何说明或 ``` 代码块。"
            )
            user = f"口播全文：\n{narration}"

        adapter = await get_ai_adapter(self.session, scene_key="script_writer")
        if not isinstance(adapter, OpenAICompatibleAdapter):
            raise AppError("LLM not configured")

        # Higher temperature so repeated clicks produce varied plans.
        raw = await adapter._chat(system, user, temperature=1.0)
        from app.adapters.ai import _extract_thinking
        _, clean = _extract_thinking(raw)
        try:
            parsed = adapter._parse_json_response(clean)
        except Exception:
            # Last-ditch: maybe the LLM wrapped in {"broll_plan": [...]}
            try:
                obj = json.loads(clean)
                parsed = obj.get("broll_plan") if isinstance(obj, dict) else obj
            except Exception as e:
                raise AppError(f"Could not parse LLM output: {str(e)[:100]}")

        if isinstance(parsed, dict):
            parsed = parsed.get("broll_plan") or []

        sanitized = _sanitize_broll_plan(parsed, sections, section_ids)
        if not sanitized:
            raise AppError("LLM produced no valid B-roll entries")

        sc["broll_plan"] = sanitized
        # repo.update returns the (flushed, still-in-session) row already
        # carrying the new structured_content — no need to re-SELECT.
        updated = await self.repo.update(creation_id, structured_content=sc)
        if not updated:
            raise NotFoundError("Creation", str(creation_id))
        return updated
