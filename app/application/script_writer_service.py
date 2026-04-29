from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import AIAdapter, OpenAICompatibleAdapter, _extract_thinking, get_ai_adapter
from app.adapters.prompt_builder import format_asset_context, format_knowledge_flat, format_strategy_focus
from app.application.context_service import ContextService
from app.application.script_composer import compose_system_prompt
from app.infrastructure.strategy_unit_repo import StrategyUnitRepository
from app.models.offer import Offer
from app.schemas.app import ScriptWriterRequest

logger = logging.getLogger(__name__)

_MAX_KNOWLEDGE_ITEMS = 15
_MAX_CONTENT_CHARS = 500

# Legacy goal key → Chinese/English label (for user message, kept for backwards compat)
_GOAL_LABELS = {
    "reach_growth": ("涨粉丝", "Grow Audience"),
    "lead_generation": ("拿线索", "Get Leads"),
    "conversion": ("卖东西", "Drive Sales"),
    "education": ("传信息", "Share Knowledge"),
    "traffic_redirect": ("引流直播间", "Drive Traffic"),
    "other": ("其他", "Other"),
    # new goal_id values (from script_goals.py)
    "seeding": ("种草", "Seeding"),
    "knowledge_sharing": ("知识分享", "Knowledge Sharing"),
    "brand_awareness": ("品牌传播", "Brand Awareness"),
}


def _build_user_message(
    request: ScriptWriterRequest,
    *,
    knowledge_text: str = "",
    strategy_text: str = "",
    asset_text: str = "",
    is_video: bool = True,
) -> str:
    """Build the user message from request parameters + context.

    ``is_video`` gates the numeric ``word_count`` line. Short video platforms
    benefit from a hard length signal (60s vs 90s vs livestream feel
    distinctly different to viewers, and word_count is the proxy for
    duration). Text platforms — wechat_gzh, blog, substack, xiaohongshu —
    each have rich prose length specs in their platform body
    ("1500-3000 字最佳", "200-800 words sweet spot"), and a numeric
    word_count anchor reliably overrides those nuanced specs in practice.
    Default is True so the legacy non-composer path (script-writer's
    original short-video flow) keeps the line.
    """
    is_en = request.language.startswith("en")
    goal_zh, goal_en = _GOAL_LABELS.get(request.goal, ("其他", "Other"))

    parts: list[str] = []

    # Required params
    if request.topic.strip():
        parts.append(f"{'topic: ' if is_en else 'topic（选题）：'}{request.topic}")
    else:
        parts.append(
            "topic: (not specified — generate based on knowledge base context and goal)"
            if is_en else
            "topic（选题）：（未指定，请根据知识库上下文和内容目标自行选题）"
        )
    parts.append(f"{'goal: ' if is_en else 'goal（内容目标）：'}{goal_en if is_en else goal_zh}")

    # Optional params
    if request.tone:
        parts.append(f"{'tone: ' if is_en else 'tone（语气风格）：'}{request.tone}")
    if is_video:
        parts.append(f"{'word_count: ' if is_en else 'word_count（字数）：'}{request.word_count}")
    if request.cta:
        parts.append(f"{'cta: ' if is_en else 'cta（引导动作）：'}{request.cta}")
    if request.industry:
        parts.append(f"{'industry: ' if is_en else 'industry（行业）：'}{request.industry}")
    if request.reference:
        parts.append(f"\n{'reference (reference script):' if is_en else 'reference（参考文案）：'}\n{request.reference}")
    if request.extra_req:
        if is_en:
            parts.append(
                f"\nextra_req (additional requirements — may contain structural hints AND "
                f"citable data like numbers, percentages, ranks, metrics):\n{request.extra_req}\n"
                f"IMPORTANT: If extra_req contains concrete figures (e.g. '26% drop', 'ranked #1'), "
                f"quote the exact numbers verbatim in the body, not only in a summary table. "
                f"Data-driven claims with specific figures convert better than vague prose."
            )
        else:
            parts.append(
                f"\nextra_req（额外要求 —— 可能包含结构提示 + 可引用数据 / 数字 / 百分比 / 排名 / 实测指标）：\n{request.extra_req}\n"
                f"重要：如果 extra_req 中含有具体数字（如『下降 26%』、『排名第一』），"
                f"请在正文中**原样引用这些数字**，不要只放在总结表里。具体数据比空话更有说服力。"
            )

    # Contextual info
    if strategy_text:
        parts.append(strategy_text)
    if knowledge_text:
        parts.append(knowledge_text)
    if asset_text:
        parts.append(asset_text)

    return "\n".join(parts)


def _sanitize_broll_plan(raw_plan, sections: dict, section_ids: list[str]) -> list[dict]:
    """Enforce the broll_plan schema the composer prompt asks for.

    The LLM routinely:
    - Writes ``insert_after_char`` as a Chinese/English sentence (the
      narration it wants the cut to land over) instead of an int.
    - Sets ``duration_seconds`` outside the 5-10 range (often 3 or 15).
    - Skips ``type``, producing untagged entries.

    Without sanitization these break the compositor downstream — some get
    coerced at Generate-Video time, others silently drop. Fix them once,
    here, at persist time, so ``creation.structured_content.broll_plan``
    stays clean and every downstream call can rely on it.

    Returns a list of entries each with:
      {type: "retention"|"illustrative", insert_after_char: int,
       duration_seconds: int (5-10), prompt: str}
    """
    if not isinstance(raw_plan, list):
        return []

    narration = "".join((sections.get(sid) or {}).get("text", "") for sid in section_ids)
    narration_len = len(narration)
    sanitized: list[dict] = []

    for entry in raw_plan:
        if not isinstance(entry, dict):
            continue

        # ── type ─────────────────────────────────────────────────
        t = str(entry.get("type") or "illustrative").strip().lower()
        if t not in ("retention", "illustrative"):
            t = "illustrative"

        # ── insert_after_char ───────────────────────────────────
        pos = entry.get("insert_after_char", 0)
        if isinstance(pos, bool):
            pos = 0  # bool is a subclass of int; guard against accidental True/False
        if isinstance(pos, int):
            resolved = pos
        elif isinstance(pos, float):
            resolved = int(pos)
        elif isinstance(pos, str):
            needle = pos.strip()
            # Try pure-digit first
            if needle.isdigit():
                resolved = int(needle)
            elif narration and needle:
                # Substring match — the LLM probably wrote the narration
                # excerpt it wants the cut to land after
                idx = narration.find(needle)
                if idx >= 0:
                    resolved = idx + len(needle)
                else:
                    continue  # unresolvable — drop this entry
            else:
                continue
        else:
            continue
        # Clamp to narration range
        if narration_len > 0:
            resolved = max(0, min(resolved, narration_len))
        else:
            resolved = max(0, resolved)

        # ── duration_seconds ────────────────────────────────────
        dur_raw = entry.get("duration_seconds")
        try:
            dur = int(dur_raw) if dur_raw is not None else (5 if t == "retention" else 6)
        except (ValueError, TypeError):
            dur = 5 if t == "retention" else 6
        dur = max(5, min(dur, 10))
        if t == "retention":
            dur = 5  # spec-enforced: retention always 5s

        # ── prompt ───────────────────────────────────────────────
        prompt = str(entry.get("prompt") or "").strip()
        if not prompt:
            continue  # no prompt means nothing to render — drop

        sanitized.append({
            "type": t,
            "insert_after_char": resolved,
            "duration_seconds": dur,
            "prompt": prompt,
        })

    # Sort by insert position so downstream consumers see narration order
    sanitized.sort(key=lambda e: e["insert_after_char"])
    return sanitized


def _normalize_structured_content(raw: dict, platform, structure) -> dict:
    """Ensure structured JSON from LLM conforms to our schema. Best-effort."""
    sections_raw = raw.get("sections") or {}
    sections: dict = {}
    for sid in structure.section_ids:
        sec = sections_raw.get(sid) or {}
        if isinstance(sec, str):
            sec = {"text": sec}
        entry: dict = {"text": sec.get("text") or sec.get("narration") or ""}
        if platform.is_video:
            entry["visual_direction"] = sec.get("visual_direction") or ""
            entry["duration_seconds"] = sec.get("duration_seconds") or None
        else:
            if sec.get("image_hint"):
                entry["image_hint"] = sec["image_hint"]
        sections[sid] = entry

    result: dict = {
        "platform_id": platform.id,
        "structure_id": structure.id,
        "persona_id": raw.get("persona_id"),
        "goal_id": raw.get("goal_id"),
        "content_type": platform.content_type,
        "section_ids": structure.section_ids,  # preserve order (JSONB sorts keys)
        "sections": sections,
    }
    if platform.is_video and raw.get("estimated_total_seconds"):
        result["estimated_total_seconds"] = raw["estimated_total_seconds"]
    if platform.is_video and raw.get("broll_plan"):
        result["broll_plan"] = _sanitize_broll_plan(
            raw["broll_plan"], sections, structure.section_ids,
        )
    if not platform.is_video and raw.get("metadata"):
        result["metadata"] = raw["metadata"]
    return result


def _structured_content_to_plain_text(sc: dict) -> str:
    """Concatenate section texts into a single plain-text script (for TTS and display)."""
    sections = sc.get("sections") or {}
    # Use section_ids for correct order (JSONB doesn't preserve key order)
    order = sc.get("section_ids") or list(sections.keys())
    lines: list[str] = []
    for sid in order:
        sec = sections.get(sid)
        if sec:
            text = (sec.get("text") or "").strip()
            if text:
                lines.append(text)
    return "\n\n".join(lines)


# ── JSON recovery helpers (for when LLM JSON output fails to parse) ─────────

import re as _re

_TEXT_FIELD_START_RE = _re.compile(r'"text"\s*:\s*"')


def _heuristic_narration_from_json_string(raw: str) -> str:
    """Best-effort extraction of narration from a malformed JSON string.

    Finds each `"text": "..."` and captures the value, handling:
      - Standard JSON escapes (\\", \\\\, \\n)
      - **Unclosed strings** (LLM output truncated mid-sentence) — captures
        what we have and moves on, rather than dropping that section entirely
    """
    if not raw:
        return ""

    captured: list[str] = []
    for start_match in _TEXT_FIELD_START_RE.finditer(raw):
        i = start_match.end()
        buf = []
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw):
                # Include escape sequence as-is (json.loads will handle it later)
                buf.append(raw[i:i+2])
                i += 2
                continue
            if ch == '"':
                break  # end of string
            buf.append(ch)
            i += 1
        segment = "".join(buf)
        if segment:
            # Try proper JSON decoding (handles \n, \", etc.)
            try:
                decoded = json.loads(f'"{segment}"')
            except Exception:
                # Malformed escape at tail — drop the last fragment after last backslash
                safe = segment.rsplit("\\", 1)[0] if "\\" in segment else segment
                try:
                    decoded = json.loads(f'"{safe}"')
                except Exception:
                    decoded = segment
            captured.append(decoded.strip())

    return "\n\n".join(s for s in captured if s)


def _parse_markdown_to_structured_content(text: str, platform) -> dict:
    """Parse plain markdown output (from non-video platforms) into structured_content.

    Conventions:
      - First `# ...` line = title
      - A single-line of `#tag1 #tag2 ...` (all tokens start with #) = hashtags
      - Everything else = body text
      - content_type=thread: body is split by `---` separator lines → each part = one section
    """
    ct = platform.content_type
    raw = (text or "").strip()
    # Strip any accidental code fence the LLM might have added
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    title = None
    hashtags: list[str] = []
    body_lines: list[str] = []

    for line in raw.split("\n"):
        stripped = line.strip()
        # H1 title (only first one)
        if title is None and stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip()
            continue
        # Hashtag line: all whitespace-separated tokens start with # and have no space
        if stripped and stripped.count("#") >= 2:
            tokens = stripped.split()
            if tokens and all(tok.startswith("#") and len(tok) > 1 for tok in tokens):
                hashtags.extend(tokens)
                continue
        body_lines.append(line)

    body = "\n".join(body_lines).strip()

    # Build sections
    if ct == "thread":
        parts = [p.strip() for p in _re.split(r"\n---\n", body) if p.strip()]
        sections = {f"tweet_{i+1}": {"text": p} for i, p in enumerate(parts)}
        section_ids = list(sections.keys())
    else:
        sections = {"body": {"text": body}}
        section_ids = ["body"]

    result: dict = {
        "platform_id": platform.id,
        "content_type": ct,
        "section_ids": section_ids,
        "sections": sections,
    }
    metadata = {}
    if title:
        metadata["title"] = title
    if hashtags:
        metadata["hashtags"] = hashtags
    if metadata:
        result["metadata"] = metadata
    return result


def _structured_content_to_plain_text_with_metadata(sc: dict) -> str:
    """Render structured_content (from markdown parse) back to copy-ready plain text.

    Used for Creation.content — the text that gets copied/displayed.
    Format: title (if any) + body sections + hashtags (if any).
    """
    parts = []
    meta = sc.get("metadata") or {}
    if meta.get("title"):
        parts.append(meta["title"])
    body = _structured_content_to_plain_text(sc)
    if body:
        parts.append(body)
    if meta.get("hashtags"):
        parts.append(" ".join(meta["hashtags"]))
    return "\n\n".join(parts)


_INLINE_JSON_ARTIFACT_RE = _re.compile(
    # Match the typical Claude / OpenAI-compat JSON-mode confusion artifact:
    # ``...prose```json\n{\n``  →  prose then a half-attempted JSON opening.
    # Also matches the closing variant ``\n}\n```\n``.
    # Optional whitespace and {/} around the fence so we tolerate model variants.
    r'```\s*(?:json)?\s*\n?\{?\s*\n?|\n?\s*\}?\s*\n?```',
    flags=_re.IGNORECASE,
)


def _scrub_json_artifacts(text: str) -> str:
    """Final safety net: never let raw JSON-looking text reach Creation.content.

    1. Strip a leading ```json…``` markdown fence (whole-string fence).
    2. If the body still looks like a JSON object, run the heuristic to
       pull narration text out of ``"text": "..."`` fields.
    3. Strip *inline* ```json {`` artifacts — the typical Claude JSON-mode
       failure where the model wrote prose, then a half-attempted JSON
       opening, then more prose. Split on the artifact and keep the
       longest narrative chunk (usually the more polished retry).
    4. Final fallback: trimmed text.
    """
    if not text:
        return text
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    if stripped.startswith("{") and '"text"' in stripped:
        extracted = _heuristic_narration_from_json_string(stripped)
        if extracted:
            stripped = extracted
    if "```" in stripped:
        # Inline JSON fence cleanup. We split on the artifact and keep
        # the longest meaningful piece — both halves are usually drafts
        # of the same narration, picking the longer one gives the user
        # the most complete version.
        chunks = _INLINE_JSON_ARTIFACT_RE.split(stripped)
        chunks = [c.strip() for c in chunks if c and c.strip()]
        narrative = [
            c for c in chunks
            # Drop pieces that are JSON scaffolding only (braces /
            # whitespace / commas).
            if not _re.fullmatch(r'[\s{}\[\],]+', c) and len(c) >= 20
        ]
        if narrative:
            stripped = max(narrative, key=len)
    return stripped


DEFAULT_SYSTEM_PROMPT_ZH = (
    "你是一位专业的短视频口播文案创作专家。根据用户提供的参数，生成可直接用于数字人或真人拍摄的口播文案。"
    "为嘴巴写字，不为眼睛写字。禁止书面语，用口语化表达。开头3秒必须抓住观众。"
    "单句不超过30字，适配TTS合成（用逗号句号断句，禁用省略号、破折号）。"
    "直接输出文案正文，不加标题、不加引号、不加前缀。"
    "输出语言必须与知识库内容和选题的语言保持一致。"
)

DEFAULT_SYSTEM_PROMPT_EN = (
    "You are an expert short-video scriptwriter. Generate spoken-word scripts for digital human or live presenter recordings. "
    "Write for the mouth, not the eye. Use conversational language. The first 3 seconds must hook the viewer. "
    "Keep sentences under 20 words. Optimize for TTS (use commas and periods for pacing, no ellipses or em dashes). "
    "Output the script text directly — no title, no quotes, no preamble. "
    "Output language MUST match the knowledge base content language."
)


class ScriptWriterService:
    def __init__(self, session: AsyncSession, ai_adapter: AIAdapter | None = None):
        self.session = session
        self.ai = ai_adapter

    async def _prepare(self, request: ScriptWriterRequest):
        """Load context, build user message, resolve adapter.

        Returns (adapter, system_prompt, user_message, knowledge_count, platform, structure)
        where platform and structure are None when composer dimensions are not set.
        """
        if not self.ai:
            self.ai = await get_ai_adapter(
                self.session,
                scene_key="script_writer",
                config_id=request.config_id,
                model_override=request.model_override,
            )

        logger.info(
            "ScriptWriter: using adapter %s/%s for offer %s",
            getattr(self.ai, "provider", "?"),
            getattr(self.ai, "model", "?"),
            request.offer_id,
        )

        # Load offer context + knowledge
        ctx_service = ContextService(self.session)
        context = await ctx_service.get_offer_context(request.offer_id)

        knowledge_items = []
        kb_sample_parts = []
        for k in context.knowledge_items:
            content = (k.content_raw or "")[:_MAX_CONTENT_CHARS]
            knowledge_items.append({
                "knowledge_type": k.knowledge_type,
                "title": k.title,
                "content_raw": content,
            })
            kb_sample_parts.append((k.title or "") + " " + content)

        # KB-centric language rule: if ``request.language`` is set the
        # caller explicitly picked; otherwise follow the KB's detected
        # content language.
        from app.libs.lang_detect import resolve_output_language
        request.language = resolve_output_language(
            request.language,
            " ".join(kb_sample_parts[:_MAX_KNOWLEDGE_ITEMS]),
            caller="script_writer",
        )

        knowledge_text = format_knowledge_flat(
            knowledge_items[:_MAX_KNOWLEDGE_ITEMS], language=request.language
        )

        # Load strategy unit context if provided
        strategy_text = ""
        if request.strategy_unit_id:
            su_repo = StrategyUnitRepository(self.session)
            unit = await su_repo.get_by_id(request.strategy_unit_id)
            if unit:
                su_dict = {
                    "name": unit.name,
                    "marketing_objective": unit.marketing_objective,
                    "audience_segment": unit.audience_segment,
                    "scenario": unit.scenario,
                    "channel": unit.channel,
                    "notes": unit.notes,
                }
                strategy_text = format_strategy_focus(su_dict, language=request.language)

        # Format asset content as supplementary context
        asset_text = format_asset_context(
            context.assets, language=request.language
        )

        # When a topic_plan_id is provided (typically arriving from Topic
        # Studio's "做视频"/"写文案" links), enrich the prompt with the
        # plan's structural fields (angle, key_points, audience, scenario).
        # The user's typed topic stays as the headline; metadata follows.
        # Trend signals (hotspot, do_not_associate, relevance_tier) are
        # handled separately below by ``resolve_trend_context`` so they
        # work for both the topic_plan_id-inheritance path AND the
        # direct external_context_text path (user's own trend input).
        if request.topic_plan_id:
            try:
                from app.models.topic_plan import TopicPlan
                plan = await self.session.get(TopicPlan, request.topic_plan_id)
                if plan:
                    parts: list[str] = []
                    base = (request.topic or "").strip()
                    if base:
                        parts.append(base)
                    else:
                        if plan.title:
                            parts.append(f"选题标题: {plan.title}")
                        if plan.hook:
                            parts.append(f"开场钩子: {plan.hook}")
                    if plan.angle:
                        parts.append(f"切入角度: {plan.angle}")
                    if plan.key_points_json:
                        kp = plan.key_points_json if isinstance(plan.key_points_json, list) else []
                        if kp:
                            parts.append("要点:\n- " + "\n- ".join(str(p) for p in kp))
                    if plan.target_audience_json:
                        ta = plan.target_audience_json if isinstance(plan.target_audience_json, list) else []
                        if ta:
                            parts.append("目标人群: " + ", ".join(str(p) for p in ta))
                    if plan.target_scenario_json:
                        ts = plan.target_scenario_json if isinstance(plan.target_scenario_json, list) else []
                        if ts:
                            parts.append("适用场景: " + ", ".join(str(p) for p in ts))
                    if parts:
                        request.topic = "\n".join(parts)
                        logger.info(
                            "ScriptWriter: enriched topic from plan %s (had_user_topic=%s, source_mode=%s)",
                            request.topic_plan_id, bool(base), plan.source_mode,
                        )
            except Exception as e:
                logger.warning("ScriptWriter: failed to load topic_plan %s: %s", request.topic_plan_id, e)

        # Trend-bridge resolution — unified for direct input AND inherited
        # from topic_plan_id. Direct text wins when both are present, but
        # the inherited hotspot still feeds keywords / public_attention /
        # risk_zones into the prompt so the LLM has a structured
        # trend read alongside whatever raw text the user pasted.
        from app.adapters.prompt_builder import (
            format_trend_system_block,
            format_trend_user_block,
            resolve_trend_context,
        )
        trend = await resolve_trend_context(
            self.session,
            topic_plan_id=request.topic_plan_id,
            external_context_text=request.external_context_text,
            external_context_url=request.external_context_url,
        )
        trend_user_block = ""
        if trend.is_active:
            trend_user_block = format_trend_user_block(trend, language=request.language)
            logger.info(
                "ScriptWriter: trend-bridge active (direct=%s, inherited_hotspot=%s)",
                bool(trend.external_context_text), bool(trend.hotspot),
            )

        # Resolve the platform's content_type early so we can decide
        # whether to inject the numeric word_count signal. Lookup is
        # cheap — the registry is process-cached. Defaults to "video"
        # when no platform_id is set (legacy script-writer path).
        is_video_request = True
        if request.platform_id:
            from app.application.script_platforms import get_platform
            _p = get_platform(request.platform_id)
            if _p is not None:
                is_video_request = _p.is_video

        user_message = _build_user_message(
            request,
            knowledge_text=knowledge_text,
            strategy_text=strategy_text,
            asset_text=asset_text,
            is_video=is_video_request,
        )
        if trend_user_block:
            user_message = user_message + "\n" + trend_user_block

        # Build system prompt: use Composer when any dimension is specified,
        # otherwise fall back to the legacy system_prompt field.
        platform = None
        structure = None
        use_composer = any([
            request.platform_id, request.persona_id, request.goal_id, request.structure_id
        ])

        if use_composer:
            brand_voice = await ctx_service.resolve_brand_voice(request.offer_id)
            system_prompt, platform, structure = compose_system_prompt(
                platform_id=request.platform_id,
                persona_id=request.persona_id,
                goal_id=request.goal_id or request.goal,
                structure_id=request.structure_id,
                language=request.language,
                brand_tone=brand_voice,
            )
        else:
            # Legacy path: use system_prompt from request (or built-in default)
            system_prompt = request.system_prompt or (
                DEFAULT_SYSTEM_PROMPT_ZH if not request.language.startswith("en")
                else DEFAULT_SYSTEM_PROMPT_EN
            )

        # Append the trend-bridge system block when trend is active. Goes
        # AFTER the brand voice / composer instructions so the trend
        # stance ("solution naturally appears") is the last thing the
        # model reads before output — sandwich-anchor pattern.
        if trend.is_active:
            system_prompt = system_prompt + format_trend_system_block(
                mode="script_gen", language=request.language
            )

        return self.ai, system_prompt, user_message, len(knowledge_items), platform, structure

    async def generate(self, request: ScriptWriterRequest) -> dict:
        """Non-streaming generation. Returns {"script": str, "knowledge_count": int, "structured_content": dict|None}."""
        adapter, system_prompt, user_message, knowledge_count, platform, structure = await self._prepare(request)

        if not isinstance(adapter, OpenAICompatibleAdapter):
            return {"script": "[Stub] No LLM configured.", "knowledge_count": knowledge_count, "structured_content": None}

        structured_content = None
        if platform is not None and structure is not None:
            if platform.is_video:
                # Video: JSON mode (needed for B-roll)
                try:
                    raw_json = await adapter._chat_json(system_prompt, user_message, temperature=0.75)
                    structured_content = _normalize_structured_content(raw_json, platform, structure)
                    clean_text = _structured_content_to_plain_text(structured_content)
                except Exception as e:
                    logger.warning("ScriptWriter: JSON generation failed (%s), falling back to plain text", e)
                    result = await adapter._chat(system_prompt, user_message, temperature=0.8)
                    _, clean_text = _extract_thinking(result)
                    structured_content = None
            else:
                # Text (post/article/thread): markdown mode
                result = await adapter._chat(system_prompt, user_message, temperature=0.8)
                _, md_text = _extract_thinking(result)
                structured_content = _parse_markdown_to_structured_content(md_text, platform)
                clean_text = _structured_content_to_plain_text_with_metadata(structured_content)
        else:
            # Legacy path: plain text
            result = await adapter._chat(system_prompt, user_message, temperature=0.8)
            _, clean_text = _extract_thinking(result)

        creation_id = None
        if request.save_creation and clean_text.strip():
            creation_id = await self._save_creation(request, clean_text, structured_content)

        return {
            "script": clean_text,
            "knowledge_count": knowledge_count,
            "structured_content": structured_content,
            "creation_id": creation_id,
        }

    async def _save_creation(
        self,
        request: ScriptWriterRequest,
        content: str,
        structured_content: dict | None,
    ) -> str | None:
        """Persist script output as a Creation. Returns creation id string or None."""
        from app.infrastructure.creation_repo import CreationRepository

        try:
            # Resolve merchant_id from offer
            offer = await self.session.get(Offer, request.offer_id)
            if not offer:
                return None
            merchant_id = offer.merchant_id

            # Build title from topic or first line of content
            title = (request.topic or content.split("\n")[0])[:120].strip()
            if not title:
                title = "Script"

            # Determine content_type for the creation
            content_type = "script_writer"
            if structured_content:
                ct = structured_content.get("content_type", "video")
                content_type = f"script_{ct}"  # e.g. "script_video", "script_text_post"

            repo = CreationRepository(self.session)
            creation = await repo.create(
                merchant_id=merchant_id,
                offer_id=request.offer_id,
                title=title,
                content=content,
                content_type=content_type,
                source_app=request.source_app or "script_writer",
                structured_content=structured_content,
            )
            await self.session.commit()
            logger.info("ScriptWriter: saved creation %s", creation.id)
            return str(creation.id)
        except Exception as e:
            logger.warning("ScriptWriter: failed to save creation: %s", e)
            await self.session.rollback()
            return None

    async def suggest_topic(
        self,
        offer_id: str,
        strategy_unit_id: str | None = None,
        goal: str = "reach_growth",
        language: str | None = None,
        config_id: str | None = None,
        model_override: str | None = None,
    ) -> str:
        """Use LLM to suggest a topic based on knowledge base + strategy context."""
        import uuid as _uuid

        if not self.ai:
            self.ai = await get_ai_adapter(
                self.session,
                scene_key="script_writer",
                config_id=config_id,
                model_override=model_override,
            )

        goal_zh, goal_en = _GOAL_LABELS.get(goal, ("其他", "Other"))

        # Load knowledge
        ctx_service = ContextService(self.session)
        context = await ctx_service.get_offer_context(_uuid.UUID(offer_id))

        knowledge_items = []
        kb_raw_sample = []
        for k in context.knowledge_items:
            content = (k.content_raw or "")[:_MAX_CONTENT_CHARS]
            knowledge_items.append({
                "knowledge_type": k.knowledge_type,
                "title": k.title,
                "content_raw": content,
            })
            kb_raw_sample.append((k.title or "") + " " + content)

        # Presence-of-language rule (matches _prepare): explicit wins, else KB.
        from app.libs.lang_detect import resolve_output_language
        effective_language = resolve_output_language(
            language, " ".join(kb_raw_sample[:_MAX_KNOWLEDGE_ITEMS]),
            caller="suggest_topic",
        )
        is_en = effective_language.startswith("en")

        knowledge_text = format_knowledge_flat(
            knowledge_items[:_MAX_KNOWLEDGE_ITEMS], language=effective_language
        )

        # Load strategy unit
        strategy_text = ""
        if strategy_unit_id:
            su_repo = StrategyUnitRepository(self.session)
            unit = await su_repo.get_by_id(_uuid.UUID(strategy_unit_id))
            if unit:
                su_dict = {
                    "name": unit.name,
                    "marketing_objective": unit.marketing_objective,
                    "audience_segment": unit.audience_segment,
                    "scenario": unit.scenario,
                    "channel": unit.channel,
                    "notes": unit.notes,
                }
                strategy_text = format_strategy_focus(su_dict, language=effective_language)

        offer_name = context.offer.name

        # Format asset content as supplementary context
        asset_text = format_asset_context(
            context.assets, language=effective_language
        )

        if is_en:
            system = (
                "You are a creative short-video content planner. "
                "Suggest ONE specific, compelling topic for a spoken-word video script. "
                "Return ONLY the topic text, nothing else — no quotes, no explanation. "
                "**Output language: English.** Even if the knowledge base below contains "
                "Chinese phrases or mixed content, your topic MUST be written in English."
            )
            # Dual-anchor the language directive (top + bottom of the user
            # message) — previous runs saw the LLM mirror the KB's dominant
            # language when instructions lived only in the system prompt,
            # producing Chinese topics for English KBs with Chinese-named
            # products or mixed KB entries.
            user = (
                "OUTPUT LANGUAGE: English. Do not produce any Chinese characters.\n\n"
                f"Product: {offer_name}\n"
                f"Goal: {goal_en}\n"
                f"{strategy_text}\n{knowledge_text}\n{asset_text}\n\n"
                "Based on the product info, knowledge base, and goal above, "
                "suggest one specific, creative topic for a short video script. "
                "Be concrete — not generic. Output the topic only.\n\n"
                "Reminder: output the topic in English only."
            )
        else:
            system = (
                "你是一位短视频内容策划专家。"
                "根据提供的信息，推荐一个具体的、有吸引力的口播选题。"
                "只返回选题文字本身，不要加引号、不要解释。"
                "**输出语言：中文。** 即便知识库里混有英文片段，输出的选题也必须是中文。"
            )
            user = (
                "输出语言：中文。选题中不得出现英文整句。\n\n"
                f"商品：{offer_name}\n"
                f"内容目标：{goal_zh}\n"
                f"{strategy_text}\n{knowledge_text}\n{asset_text}\n\n"
                "根据以上商品信息、知识库和目标，推荐一个具体的、有创意的口播选题。"
                "要具体，不要泛泛而谈。只输出选题本身。\n\n"
                "提醒：选题只用中文输出。"
            )

        # Brand voice overlay — keeps the single-topic suggestion on-brand.
        from app.adapters.prompt_builder import format_brand_voice_layer
        brand_voice = await ctx_service.resolve_brand_voice(_uuid.UUID(offer_id))
        system += format_brand_voice_layer(brand_voice, effective_language)

        if isinstance(self.ai, OpenAICompatibleAdapter):
            # Use streaming to collect the response — non-streaming can return empty content
            # for thinking models (e.g. Qwen3 via Ollama puts reasoning in reasoning_content
            # and leaves content empty, while streaming sends everything through delta.content).
            tokens = []
            async for token in self.ai._chat_stream(system, user, temperature=0.9):
                tokens.append(token)
            result = "".join(tokens)
        else:
            # Stub fallback
            result = f"{'How ' + offer_name + ' helps you achieve more' if is_en else offer_name + '的3个你不知道的用法'}"
        _, clean = _extract_thinking(result)
        # Fallback: model thought but produced no answer after </think>
        if not clean.strip() and "<think>" in result:
            thinking_raw = result.split("<think>", 1)[-1].split("</think>", 1)[0]
            lines = [l.strip() for l in thinking_raw.splitlines() if l.strip() and not l.strip().startswith("-")]
            clean = lines[-1] if lines else ""
        logger.info("suggest_topic: %d chars", len(clean))
        return clean.strip().strip('"').strip("'").strip("《》")

    async def generate_stream(self, request: ScriptWriterRequest) -> AsyncIterator[str]:
        """Yield SSE events: thinking, thinking_done, token, done."""
        t0 = time.monotonic()
        adapter, system_prompt, user_message, knowledge_count, platform, structure = await self._prepare(request)

        # Non-streaming fallback for StubAIAdapter
        if not isinstance(adapter, OpenAICompatibleAdapter):
            is_en = request.language.startswith("en")
            goal_zh, goal_en = _GOAL_LABELS.get(request.goal, ("其他", "Other"))
            stub_text = (
                f"[Stub] Script generation is not available without an LLM configured.\n\n"
                f"Topic: {request.topic}\nGoal: {goal_en if is_en else goal_zh}\nWord count: {request.word_count}"
                if is_en else
                f"[Stub] 未配置 LLM，无法生成文案。\n\n"
                f"选题：{request.topic}\n目标：{goal_zh}\n字数：{request.word_count}"
            )
            result = {"script": stub_text, "knowledge_count": knowledge_count, "structured_content": None}
            yield f"event: done\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
            return

        # Stream tokens — wrap the LLM call so any failure (timeout,
        # connect error, rate limit) becomes a terminal SSE error event
        # the frontend can render. Without this, an exception drops the
        # stream silently and the user sees a stuck spinner with no
        # explanation.
        full_output = ""
        state = "before_think"  # before_think → in_think → after_think / no_think
        try:
            async for token in adapter._chat_stream(
                system_prompt, user_message, temperature=0.8, timeout=600
            ):
                full_output += token

                if state == "before_think":
                    if "<think>" in full_output:
                        state = "in_think"
                        after_tag = full_output.split("<think>", 1)[1]
                        if after_tag:
                            yield f"event: thinking\ndata: {json.dumps(after_tag, ensure_ascii=False)}\n\n"
                    elif len(full_output) > 20 and "<" not in full_output:
                        state = "no_think"
                        yield f"event: token\ndata: {json.dumps(full_output, ensure_ascii=False)}\n\n"

                elif state == "in_think":
                    if "</think>" in full_output:
                        before_close = token.split("</think>")[0]
                        if before_close:
                            yield f"event: thinking\ndata: {json.dumps(before_close, ensure_ascii=False)}\n\n"
                        state = "after_think"
                        yield "event: thinking_done\ndata: {}\n\n"
                        after_close = token.split("</think>")[-1] if "</think>" in token else ""
                        if after_close.strip():
                            yield f"event: token\ndata: {json.dumps(after_close, ensure_ascii=False)}\n\n"
                    else:
                        yield f"event: thinking\ndata: {json.dumps(token, ensure_ascii=False)}\n\n"

                elif state in ("after_think", "no_think"):
                    yield f"event: token\ndata: {json.dumps(token, ensure_ascii=False)}\n\n"
        except Exception as e:
            # Reach here on LLM timeout / proxy error / rate limit etc.
            # Surface a structured error to the client so the spinner
            # stops and the toast tells the user what went wrong.
            logger.exception("ScriptWriter stream: LLM call failed")
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
        if state == "in_think":
            yield "event: thinking_done\ndata: {}\n\n"

        thinking, clean_text = _extract_thinking(full_output)
        logger.info(
            "ScriptWriter: generated %d chars in %.1fs, thinking=%d chars",
            len(clean_text), elapsed, len(thinking),
        )

        # Parse structured content if composer was used
        structured_content = None
        if platform is not None and structure is not None:
            if platform.is_video:
                # Video: JSON mode (B-roll requires structured data)
                try:
                    raw_json = adapter._parse_json_response(clean_text)
                    structured_content = _normalize_structured_content(raw_json, platform, structure)
                    clean_text = _structured_content_to_plain_text(structured_content)
                except Exception as e:
                    logger.warning(
                        "ScriptWriter stream: JSON parse failed, attempting heuristic text extraction: %s",
                        e,
                    )
                    fallback = _heuristic_narration_from_json_string(clean_text)
                    if fallback:
                        logger.info("ScriptWriter stream: recovered %d chars from heuristic", len(fallback))
                        clean_text = fallback
                # Final safety gate — never save raw JSON as narration
                clean_text = _scrub_json_artifacts(clean_text)
            else:
                # Text (post/article/thread): markdown mode
                structured_content = _parse_markdown_to_structured_content(clean_text, platform)
                clean_text = _structured_content_to_plain_text_with_metadata(structured_content)

        creation_id = None
        if request.save_creation and clean_text.strip():
            creation_id = await self._save_creation(request, clean_text, structured_content)

        result = {
            "script": clean_text,
            "knowledge_count": knowledge_count,
            "structured_content": structured_content,
            "creation_id": creation_id,
        }
        yield f"event: done\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
