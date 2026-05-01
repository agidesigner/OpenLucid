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

    async def regenerate_broll_plan(
        self,
        creation_id: uuid.UUID,
        *,
        constraint: str | None = None,
    ) -> Creation:
        """Ask the LLM to propose a new ``broll_plan`` for this creation's
        existing script. Re-uses the composer's shot-design rules so the new
        plan follows the same distribution discipline. Persists the sanitized
        result onto ``structured_content.broll_plan``.

        Script text / structure_id / platform_id stay unchanged — only the
        broll_plan array is replaced. Users hit this when the current plan
        doesn't match their taste and they'd rather not rewrite the script.

        ``constraint`` is an optional natural-language steer ("more dynamic",
        "fewer cuts", "show product close-ups") woven into the system prompt
        so the user can guide the regeneration instead of just rolling dice.
        """
        import json
        from app.adapters.ai import OpenAICompatibleAdapter, get_ai_adapter
        from app.application.script_writer_service import _sanitize_broll_plan

        creation = await self.get(creation_id)
        sc = dict(creation.structured_content or {})
        sections = sc.get("sections") or {}
        section_ids = sc.get("section_ids") or list(sections.keys())
        if not sections or not section_ids:
            raise AppError(
                "BROLL_NO_SECTIONS",
                "This creation has no script sections to plan B-roll over",
                400,
            )

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

        constraint_clean = (constraint or "").strip()
        if is_en:
            constraint_clause = (
                f"\nUSER STEER (must honor): {constraint_clean}\n"
                if constraint_clean else ""
            )
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
                f"{constraint_clause}"
                "Output JSON array only — no prose, no code fences."
            )
            user = f"Narration:\n{narration}"
        else:
            constraint_clause = (
                f"\n用户改写指令（必须遵循）：{constraint_clean}\n"
                if constraint_clean else ""
            )
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
                "最后 CTA 段不要放 B-roll。"
                f"{constraint_clause}"
                "只输出 JSON 数组，不加任何说明或 ``` 代码块。"
            )
            user = f"口播全文：\n{narration}"

        adapter = await get_ai_adapter(self.session, scene_key="script_writer")
        if not isinstance(adapter, OpenAICompatibleAdapter):
            raise AppError(
                "LLM_NOT_CONFIGURED",
                "LLM is not configured for the script_writer scene",
                500,
            )

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
                raise AppError(
                    "BROLL_PARSE_FAILED",
                    f"Could not parse LLM output: {str(e)[:100]}",
                    502,
                )

        if isinstance(parsed, dict):
            parsed = parsed.get("broll_plan") or []

        sanitized = _sanitize_broll_plan(
            parsed, sections, section_ids, est_seconds=est_seconds,
        )
        if not sanitized:
            raise AppError(
                "BROLL_EMPTY_OUTPUT",
                "LLM produced no valid B-roll entries",
                502,
            )

        sc["broll_plan"] = sanitized
        # repo.update returns the (flushed, still-in-session) row already
        # carrying the new structured_content — no need to re-SELECT.
        updated = await self.repo.update(creation_id, structured_content=sc)
        if not updated:
            raise NotFoundError("Creation", str(creation_id))
        return updated

    # ── Section-level refinement (LLM) + manual edit ─────────────────
    #
    # These two methods are the canonical "edit existing creation"
    # surface. Web / MCP / CLI all wrap one of these — the trunk takes
    # plain types only (no Request DTOs), so any interface composes
    # the call by unpacking its own request shape into these args.
    #
    # Invariants both methods enforce:
    #   1. Only sections in creation.structured_content.section_ids may
    #      be touched. Other ids → 400.
    #   2. structured_content.sections[<id>].text is updated; all OTHER
    #      fields on that section (visual_direction, duration_seconds,
    #      image_hint) are preserved unchanged.
    #   3. Creation.content (the plain-text mirror) is rebuilt from
    #      structured_content in the same transaction. Single source of
    #      truth: structured_content. The plain mirror is derived.

    @staticmethod
    def _rebuild_plain_content(sc: dict) -> str:
        """Mirror Creation.content from current structured_content.
        Branches on content_type so video gets section concat, text
        platforms get title+body+hashtags (matches _save_creation
        behavior in script_writer_service)."""
        from app.application.script_writer_service import (
            _structured_content_to_plain_text,
            _structured_content_to_plain_text_with_metadata,
        )
        if (sc or {}).get("content_type") == "video":
            return _structured_content_to_plain_text(sc)
        return _structured_content_to_plain_text_with_metadata(sc)

    async def update_section_text(
        self,
        creation_id: uuid.UUID,
        section_id: str,
        new_text: str,
    ) -> Creation:
        """Manual section edit — no LLM. Updates sections[id].text +
        rebuilds the plain mirror, single transaction."""
        if not new_text or not new_text.strip():
            raise AppError("EDIT_EMPTY_TEXT", "new_text cannot be empty", 400)

        creation = await self.get(creation_id)
        sc = dict(creation.structured_content or {})
        sections = dict(sc.get("sections") or {})
        section_ids = sc.get("section_ids") or list(sections.keys())

        if section_id not in section_ids:
            raise AppError(
                "EDIT_INVALID_SECTION",
                f"Unknown section_id {section_id!r}. Valid ids: {section_ids}",
                400,
            )

        existing = dict(sections.get(section_id) or {})
        existing["text"] = new_text.strip()
        sections[section_id] = existing
        sc["sections"] = sections

        new_content = self._rebuild_plain_content(sc)
        updated = await self.repo.update(
            creation_id, structured_content=sc, content=new_content,
        )
        if not updated:
            raise NotFoundError("Creation", str(creation_id))
        logger.info(
            "update_section_text: creation=%s section=%s len=%d",
            creation_id, section_id, len(new_text),
        )
        return updated

    async def refine_sections(
        self,
        creation_id: uuid.UUID,
        section_ids: list[str],
        constraint: str,
        *,
        config_id: str | None = None,
        model_override: str | None = None,
        language: str | None = None,
    ) -> Creation:
        """LLM-driven refinement of selected sections, preserving others.

        Mirrors regenerate_broll_plan template:
          load → build prompt → LLM (JSON mode) → validate + merge → commit.

        Output JSON contains ONLY the refined sections; other sections
        stay byte-identical. Trunk only — Web / MCP / CLI wrap this.
        """
        import json
        from app.adapters.ai import OpenAICompatibleAdapter, _extract_thinking, get_ai_adapter

        # ── Validate inputs ────────────────────────────────────────
        if not section_ids:
            raise AppError("REFINE_NO_TARGET", "section_ids cannot be empty", 400)
        if not constraint or not constraint.strip():
            raise AppError("REFINE_NO_CONSTRAINT", "constraint cannot be empty", 400)

        creation = await self.get(creation_id)
        sc = dict(creation.structured_content or {})
        sections = dict(sc.get("sections") or {})
        all_section_ids = sc.get("section_ids") or list(sections.keys())
        if not sections or not all_section_ids:
            raise AppError(
                "REFINE_NO_SECTIONS",
                "This creation has no structured sections to refine",
                400,
            )

        # de-dup + preserve order
        seen = set()
        targets = [s for s in section_ids if not (s in seen or seen.add(s))]
        invalid = [s for s in targets if s not in all_section_ids]
        if invalid:
            raise AppError(
                "REFINE_INVALID_SECTION",
                f"Unknown section_id(s) {invalid}. Valid ids: {all_section_ids}",
                400,
            )

        keep_ids = [s for s in all_section_ids if s not in targets]

        # ── Resolve output language ────────────────────────────────
        is_en = bool(language and language.lower().startswith("en"))
        if not language:
            from app.libs.lang_detect import detect_text_language
            sample = "".join(
                (sections.get(sid) or {}).get("text", "") for sid in all_section_ids
            )[:2000]
            is_en = detect_text_language(sample) == "en"

        # ── Build prompt ───────────────────────────────────────────
        # Dump full sections as JSON-ish context so LLM keeps cross-
        # section coherence (tone, named entities, person), but only
        # asks it to OUTPUT the targets.
        sections_dump = json.dumps(
            {sid: (sections.get(sid) or {}).get("text", "") for sid in all_section_ids},
            ensure_ascii=False, indent=2,
        )
        # Use a delimiter-based format (NOT JSON). The narrative text we
        # generate frequently contains ASCII " / ' / \ / { / } characters
        # — embedding it inside JSON string fields needs careful escaping
        # the LLM rarely gets right (we shipped on this and it failed:
        # `跟我说："你试试..."` had an unescaped ASCII " that prematurely
        # closed the JSON string). Delimiter blocks let the model write
        # any text without any escaping.
        if is_en:
            system = (
                "You are refining an already-generated piece of content. The "
                "user wants to change ONLY the specified sections; everything "
                "else MUST stay byte-identical and is intentionally absent "
                "from your output.\n\n"
                f"Sections to KEEP unchanged (do NOT include them in output): {keep_ids}\n"
                f"Sections to REWRITE: {targets}\n"
                f"User's rewrite constraint: {constraint.strip()}\n\n"
                "Output format — ONE block per id in the REWRITE list, separated by\n"
                "delimiter lines. The first line of each block is exactly:\n"
                "  ===<section_id>===\n"
                "(three equals, the id, three equals — nothing else on that line).\n"
                "Everything between two delimiter lines is the new text for that section.\n"
                "Use any characters freely (quotes, newlines, brackets) — no escaping needed.\n"
                "Output ONLY the rewrite blocks. No JSON. No code fences. No commentary.\n"
                "No markdown header before the first delimiter.\n"
                "Match the original tone, person, named entities, and language."
            )
            user = (
                "Current full content (for cross-section coherence; do not echo back):\n"
                f"{sections_dump}"
            )
        else:
            system = (
                "你正在 refinement 一段已经生成的内容。用户只想改指定的段落，"
                "其余段落必须**一字不动**，并且**不在**你的输出中。\n\n"
                f"保留这些段落（不要在输出中包含）：{keep_ids}\n"
                f"重写这些段落：{targets}\n"
                f"用户的改写约束：{constraint.strip()}\n\n"
                "输出格式 —— rewrite 列表里**每个 id 一个块**，块之间用分隔符行隔开。\n"
                "每个块的第一行**严格**是：\n"
                "  ===<段落id>===\n"
                "（三个等号 + id + 三个等号，那一行不能有其它字符）\n"
                "两行分隔符之间的所有内容都是该段的新文本。\n"
                "可以**自由使用任何字符**（引号、换行、括号都不需要转义）。\n"
                "**只**输出 rewrite 块。不要 JSON、不要 ``` 代码块、不要解释。\n"
                "第一个分隔符之前不要有任何前言。\n"
                "保持原文语气、人称、专有名词、语言一致。"
            )
            user = f"当前全文（仅作 context，不要回显）：\n{sections_dump}"

        # ── LLM call ───────────────────────────────────────────────
        adapter = await get_ai_adapter(
            self.session,
            scene_key="script_writer",
            config_id=config_id,
            model_override=model_override,
        )
        if not isinstance(adapter, OpenAICompatibleAdapter):
            raise AppError(
                "LLM_NOT_CONFIGURED",
                "LLM is not configured for the script_writer scene",
                500,
            )

        try:
            raw = await adapter._chat(system, user, temperature=0.7)
        except Exception as e:
            # LLM connectivity issues (timeout / DNS / wrong base_url /
            # rate limit) bubble out of `_chat` as raw openai/httpx
            # exceptions. Translate to a 503 with the underlying message
            # so the UI shows something actionable instead of a generic
            # "Refinement failed".
            detail = str(e)[:200].strip() or type(e).__name__
            logger.warning(
                "refine_sections: LLM call failed for creation=%s: %s",
                creation_id, detail,
            )
            raise AppError(
                "REFINE_LLM_UNREACHABLE",
                f"Could not reach the configured LLM ({detail}). "
                "Check the active LLM config under Settings → LLM and try again.",
                503,
            ) from e

        _, clean = _extract_thinking(raw)
        clean = clean.strip()
        # Log the raw output (truncated) so when parsing fails we can
        # see what the model actually said.
        logger.info(
            "refine_sections: LLM output (creation=%s, len=%d): %s",
            creation_id, len(clean), clean[:600],
        )

        # ── Delimiter-based section extraction ─────────────────────
        # Format: ===<section_id>===\n<text>\n===<other_id>===\n...
        # Robust to any characters in the text (quotes, newlines,
        # brackets, backslashes — no escaping needed by the model).
        import re
        delim = re.compile(r"^===\s*([\w\-]+)\s*===\s*$", re.MULTILINE)
        positions = list(delim.finditer(clean))

        accepted: dict[str, str] = {}
        if positions:
            for i, m in enumerate(positions):
                sid = m.group(1)
                if sid not in targets:
                    continue  # ignore stray keep-ids the LLM might echo
                text_start = m.end()
                text_end = positions[i + 1].start() if i + 1 < len(positions) else len(clean)
                new_text = clean[text_start:text_end].strip()
                if new_text:
                    accepted[sid] = new_text

        if not accepted:
            # Surface a useful snippet so user / dev can diagnose without docker logs.
            snippet = clean[:200].replace("\n", " ").strip() or "(empty)"
            raise AppError(
                "REFINE_EMPTY_OUTPUT",
                "LLM did not produce a recognizable refinement block. "
                f"Expected `===<id>===` delimiters for each of {targets}. "
                f"Model said: {snippet!r}. Try rewording the constraint.",
                502,
            )

        # Partial-success guard. If the LLM came back with only some of the
        # requested sections, fail loud rather than silently committing the
        # subset — the caller (web modal / MCP / CLI) shows a success state
        # and the user wouldn't notice the missed section until they re-read
        # the piece. Better to surface and let them retry with a clearer
        # constraint or fewer targets at once.
        missing = [s for s in targets if s not in accepted]
        if missing:
            raise AppError(
                "REFINE_PARTIAL_OUTPUT",
                f"LLM only refined {list(accepted.keys())} but missed {missing}. "
                "No changes were applied. Retry with a clearer constraint or "
                "refine fewer sections at once.",
                502,
            )

        for sid, new_text in accepted.items():
            existing = dict(sections[sid])
            existing["text"] = new_text
            sections[sid] = existing
        sc["sections"] = sections

        new_content = self._rebuild_plain_content(sc)

        updated = await self.repo.update(
            creation_id, structured_content=sc, content=new_content,
        )
        if not updated:
            raise NotFoundError("Creation", str(creation_id))

        logger.info(
            "refine_sections: creation=%s refined=%s constraint=%r",
            creation_id, list(accepted.keys()), constraint[:80],
        )
        return updated
