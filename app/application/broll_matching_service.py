"""B-roll asset matching — finds existing user assets that perfectly fit
specific B-roll shots in a generated video script. The user's library
often already contains real product footage that's a better fit (and
free) than re-generating with an AI video model.

Matching is interface-agnostic: web modal, MCP, future CLI all wrap
``match_assets_for_broll``. Strict gates: ≥720p (short edge), duration
2-15s for video, aspect strictly matches target video aspect, and an
LLM batch rerank confirms semantic overlap between the asset's tags
and the shot's prompt.

Returns top-1 per shot or None when no candidate clears all gates."""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ai import OpenAICompatibleAdapter, get_ai_adapter
from app.exceptions import AppError, NotFoundError
from app.models.asset import Asset
from app.models.creation import Creation

logger = logging.getLogger(__name__)


# Aspect ratio gates. Slight tolerance because integer dimensions rarely
# produce exact ratios (1920×1088 = 1.765, ≠ 1.778 from 1920×1080).
_ASPECT_TOLERANCE: dict[str, tuple[float, float]] = {
    "16:9": (1.70, 1.85),
    "9:16": (0.54, 0.59),
    "1:1":  (0.95, 1.05),
}


def compute_aspect(width: int | None, height: int | None) -> str | None:
    """Map raw dimensions to one of our supported aspects, or None.

    None = doesn't strictly match any target. Such assets are excluded
    from candidates entirely — pad/crop would distort or hide content."""
    if not width or not height:
        return None
    ratio = width / height
    for label, (lo, hi) in _ASPECT_TOLERANCE.items():
        if lo <= ratio <= hi:
            return label
    return None


def resolution_label(width: int | None, height: int | None) -> str:
    """User-facing label like '1080p' / '720p'. Uses min dimension so
    portrait clips get the right label."""
    if not width or not height:
        return "?"
    short = min(width, height)
    if short >= 2160:
        return "4K"
    if short >= 1440:
        return "1440p"
    if short >= 1080:
        return "1080p"
    if short >= 720:
        return "720p"
    return f"{short}p"


def aspect_ratio_to_label(form_aspect: str | None) -> str | None:
    """Map the video form's aspect_ratio (portrait/landscape/square) or
    a canonical label string back to a canonical label."""
    if not form_aspect:
        return None
    return {
        "portrait": "9:16",
        "landscape": "16:9",
        "square": "1:1",
        "9:16": "9:16",
        "16:9": "16:9",
        "1:1": "1:1",
    }.get(form_aspect)


async def _coarse_filter_candidates(
    session: AsyncSession,
    offer_id: uuid.UUID,
    target_aspect: str,
) -> list[Asset]:
    """SQL load + Python post-filter for the strict gates. Aspect
    isn't stored as a column, so we compute it after loading. Volume
    is bounded by offer-scoped library (typically <100 assets), so
    in-Python filtering is fine.

    Gates:
    - scope = offer (the creation's offer)
    - asset_type = video (images intentionally excluded — the broll
      compositor pipeline expects timed video clips, and feeding a
      still image through `-t duration` without `-loop 1` would
      produce a single-frame segment. Image-as-broll is a future
      feature that needs compositor support; until then we don't
      want the LLM rerank to silently pick an image and break output)
    - parse_status = done (skip half-processed uploads)
    - short edge ≥ 720
    - aspect strictly matches target_aspect
    - duration 2-15s
    """
    stmt = select(Asset).where(
        Asset.scope_type == "offer",
        Asset.scope_id == offer_id,
        Asset.asset_type == "video",
        Asset.parse_status == "done",
    )
    result = await session.execute(stmt)
    raw_assets = list(result.scalars().all())

    qualified: list[Asset] = []
    for a in raw_assets:
        meta = a.metadata_json or {}
        w, h = meta.get("width"), meta.get("height")
        if not w or not h:
            continue
        if min(w, h) < 720:
            continue
        if compute_aspect(w, h) != target_aspect:
            continue
        # Always video here (SQL filter), so duration is always required.
        dur_ms = meta.get("duration_ms")
        if not dur_ms or dur_ms < 2000 or dur_ms > 15000:
            continue
        qualified.append(a)
    return qualified


def _asset_to_match_dict(a: Asset) -> dict:
    """Public-facing shape for an asset that won the rerank — kept
    intentionally small (frontend renders thumb + filename + metadata
    chips, doesn't need full asset record)."""
    meta = a.metadata_json or {}
    w, h = meta.get("width"), meta.get("height")
    dur_ms = meta.get("duration_ms")
    duration_s = round(dur_ms / 1000) if dur_ms else 0
    return {
        "id": str(a.id),
        "file_name": a.file_name,
        "preview_uri": a.preview_uri,
        "asset_type": a.asset_type,
        "duration_seconds": duration_s,
        "width": w,
        "height": h,
        "aspect": compute_aspect(w, h),
        "resolution_label": resolution_label(w, h),
    }


def _flatten_tags(tags_json: dict | None) -> list[str]:
    """Tags are stored as {category: [tag1, tag2]}. Flatten for prompt
    consumption. Cap at 20 to keep the rerank prompt tight."""
    if not isinstance(tags_json, dict):
        return []
    out: list[str] = []
    for v in tags_json.values():
        if isinstance(v, list):
            out.extend(str(t) for t in v if isinstance(t, str))
    return out[:20]


async def _llm_rerank(
    session: AsyncSession,
    broll_shots: list[dict],
    candidates: list[Asset],
) -> dict[int, str | None]:
    """One LLM call to choose the best candidate (or None) per broll
    shot. Returns: {shot_index: asset_id_or_None}.

    Rationale for batch (not per-shot): N broll shots × M candidates
    is small enough that a single prompt fits comfortably; per-shot
    LLM calls would 5× the cost for typical 5-shot videos."""
    if not candidates or not broll_shots:
        return {i: None for i in range(len(broll_shots))}

    adapter = await get_ai_adapter(session, scene_key="script_writer")
    if not isinstance(adapter, OpenAICompatibleAdapter):
        # Without LLM, we can't confirm semantic match. Returning all-
        # None is safer than picking blindly — the user can still hit
        # the AI generation path.
        logger.warning("broll_matching: LLM not configured — skipping rerank")
        return {i: None for i in range(len(broll_shots))}

    cand_summary = [
        {
            "id": str(a.id),
            "file": a.file_name,
            "type": a.asset_type,
            "tags": _flatten_tags(a.tags_json),
        }
        for a in candidates
    ]
    shots_summary = [
        {"index": i, "prompt": (s.get("prompt") or "")[:300]}
        for i, s in enumerate(broll_shots)
    ]

    system = (
        "You match B-roll shots to existing video/image assets. For each "
        "shot, you have the AI director's prompt describing what the shot "
        "should show; for each candidate asset, you have its filename and "
        "tags from the user's library.\n\n"
        "For each shot, pick the asset whose semantic content would "
        "visualize the shot's intent. **Match confidently when there is "
        "clear semantic overlap** (matching subject / setting / props / "
        "atmosphere) — the user's library was curated to be reused. Only "
        "return null if NO candidate would help at all, e.g. the shot "
        "describes a software UI but every candidate is real-world "
        "footage. Reusing a real user-shot asset is consistently better "
        "than re-generating with AI; bias toward picking a match.\n\n"
        "An asset can be assigned to AT MOST one shot — don't reuse the "
        "same asset_id across shots. If two shots both fit one asset, "
        "give it to whichever shot fits BEST.\n\n"
        "Output STRICT JSON:\n"
        '{"matches": [{"shot_index": 0, "asset_id": "..." | null, '
        '"reason": "..."}, ...]}\n'
        "One entry per shot, in shot_index order. ``reason`` is ONE short "
        "sentence (≤ 120 chars) explaining the choice — it will be shown "
        "to the user as a tooltip on the matched-asset suggestion, so "
        "write it in the user's language (Chinese if any shot prompt "
        "contains CJK, otherwise English)."
    )
    user = (
        f"Shots:\n{json.dumps(shots_summary, ensure_ascii=False, indent=2)}\n\n"
        f"Candidate assets:\n{json.dumps(cand_summary, ensure_ascii=False, indent=2)}"
    )

    # temperature=0 to keep the rerank deterministic across calls — the
    # earlier 0.3 caused identical inputs to flip between match/no-match
    # depending on dice rolls, which violated the "what I see is what
    # the API returns" contract users rely on.
    try:
        raw = await adapter._chat_json(system, user, temperature=0.0)
    except Exception as e:
        logger.warning("broll_matching: LLM rerank failed: %s", e)
        return {i: (None, "") for i in range(len(broll_shots))}

    matches_list = raw.get("matches") if isinstance(raw, dict) else None
    if not isinstance(matches_list, list):
        logger.warning("broll_matching: LLM returned malformed shape: %s", raw)
        return {i: (None, "") for i in range(len(broll_shots))}

    valid_ids = {str(a.id) for a in candidates}
    out: dict[int, tuple[str | None, str]] = {
        i: (None, "") for i in range(len(broll_shots))
    }
    used_ids: set[str] = set()
    for m in matches_list:
        if not isinstance(m, dict):
            continue
        try:
            idx = int(m.get("shot_index", -1))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(broll_shots):
            continue
        aid = m.get("asset_id")
        if aid is None or not isinstance(aid, str):
            continue
        if aid not in valid_ids or aid in used_ids:
            continue
        reason = str(m.get("reason", ""))[:200].strip()
        out[idx] = (aid, reason)
        used_ids.add(aid)
        logger.info(
            "broll_matching: shot %d → asset %s (%s)",
            idx, aid, reason[:80],
        )
    return out


async def match_assets_for_broll(
    session: AsyncSession,
    creation_id: uuid.UUID,
    target_aspect: str,
) -> list[dict]:
    """Top-level entry. Returns one entry per broll shot:

      [{"shot_index": int, "matched_asset": {...} | None}, ...]

    matched_asset is None when no candidate clears the strict gates or
    the LLM rerank explicitly chose null."""
    creation = await session.get(Creation, creation_id)
    if not creation:
        raise NotFoundError("Creation", str(creation_id))
    if not creation.offer_id:
        # No offer = no asset scope; nothing to match against.
        return []

    sc = creation.structured_content or {}
    broll_plan = sc.get("broll_plan") or []
    if not broll_plan:
        return []

    target = aspect_ratio_to_label(target_aspect)
    if target is None or target not in _ASPECT_TOLERANCE:
        raise AppError(
            "BROLL_MATCH_BAD_ASPECT",
            f"Unsupported aspect {target_aspect!r}. "
            f"Use portrait/landscape/square or {list(_ASPECT_TOLERANCE)}.",
            400,
        )

    candidates = await _coarse_filter_candidates(session, creation.offer_id, target)
    logger.info(
        "broll_matching: creation=%s aspect=%s candidates=%d shots=%d",
        creation_id, target, len(candidates), len(broll_plan),
    )

    if not candidates:
        return [
            {"shot_index": i, "matched_asset": None}
            for i in range(len(broll_plan))
        ]

    chosen = await _llm_rerank(session, broll_plan, candidates)
    by_id = {str(a.id): a for a in candidates}

    out: list[dict] = []
    for i in range(len(broll_plan)):
        entry = chosen.get(i, (None, ""))
        aid, reason = entry if isinstance(entry, tuple) else (entry, "")
        if aid and aid in by_id:
            asset_dict = _asset_to_match_dict(by_id[aid])
            # Surface the LLM's one-sentence rationale so the UI can
            # show it as a tooltip on the matched-asset suggestion —
            # users want to know "why this asset?" without reading the
            # backend logs. Optional, may be empty if LLM omitted it.
            if reason:
                asset_dict["match_reason"] = reason
            out.append({
                "shot_index": i,
                "matched_asset": asset_dict,
            })
        else:
            out.append({"shot_index": i, "matched_asset": None})
    return out


async def get_asset_url_for_broll(
    session: AsyncSession,
    asset_id: uuid.UUID,
    expected_aspect: str | None = None,
) -> tuple[str, dict]:
    """Resolve an asset_id to a file URL + metadata for direct-use in
    video synthesis. Used by video_service when broll item carries
    ``asset_id`` + ``asset_mode="direct"``.

    expected_aspect (when provided) re-validates aspect at submit time
    in case the user changed video orientation after picking the asset.
    Raises AppError on mismatch.

    Returns (storage_uri_or_url, metadata_dict)."""
    asset = await session.get(Asset, asset_id)
    if not asset:
        raise NotFoundError("Asset", str(asset_id))

    meta = asset.metadata_json or {}
    w, h = meta.get("width"), meta.get("height")
    actual_aspect = compute_aspect(w, h)

    if expected_aspect:
        target = aspect_ratio_to_label(expected_aspect)
        if target and actual_aspect != target:
            raise AppError(
                "BROLL_ASSET_ASPECT_MISMATCH",
                f"Asset {asset.file_name} aspect {actual_aspect} doesn't "
                f"match target {target}. The video orientation changed "
                f"after the asset was picked — pick again or switch back.",
                400,
            )

    if not asset.storage_uri:
        raise AppError(
            "BROLL_ASSET_NO_STORAGE",
            f"Asset {asset.file_name} has no storage_uri (still uploading?)",
            400,
        )

    return asset.storage_uri, {
        "width": w,
        "height": h,
        "duration_ms": meta.get("duration_ms"),
        "asset_type": asset.asset_type,
        "file_name": asset.file_name,
    }
