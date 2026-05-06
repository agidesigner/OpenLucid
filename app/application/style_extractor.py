"""Style anchor extractor — translates an offer's reference posters into
a rich style summary that gets folded into the AI image prompt.

Two layers of evidence, both run when possible:

  1. **PIL color extraction** (always runs, no LLM).
     K-means quantization on the top reference poster yields 4-5
     dominant hex codes. Cheap, deterministic, no provider call. This
     alone is the most actionable signal a generator can consume.

  2. **Vision-LLM structured description** (best-effort).
     Sends 1-2 reference posters to the active OpenAI-compatible
     vision model and asks for composition / lighting / mood /
     visual_motifs / typography_style. When the proxy or the model
     can't handle vision input (some corporate proxies block it),
     this layer is skipped silently — the renderer still has color
     palette + tag aggregates to work from.

Output is cached on ``brandkits.style_anchor_json`` keyed by
selling_point so subsequent renders reuse it. Cache invalidation is
the caller's responsibility (mark dirty when assets change).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import uuid
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import LocalStorageAdapter
from app.models.asset import Asset
from app.models.brandkit import BrandKit
from app.models.llm_config import LLMConfig

logger = logging.getLogger(__name__)


async def get_style_anchor(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID,
    selling_point: str,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return a style summary dict for the given offer + selling_point.

    Output shape (best effort — fields missing on pipeline failure):
        {
          "subjects":  [str, ...],         # tag-derived: what should appear
          "moods":     [str, ...],         # tag-derived: campaign vibes
          "forms":     [str, ...],
          "channels":  [str, ...],
          "palette":   ["#FAF6F0", ...],   # PIL K-means
          "composition":      str | None,  # vision LLM
          "lighting":         str | None,  # vision LLM
          "vibe":             str | None,  # vision LLM (mood synonym; richer)
          "visual_motifs":    [str, ...],  # vision LLM
          "typography_style": str | None,  # vision LLM
          "reference_count": int,
        }
    """
    cache_key = (selling_point or "").strip()[:200]

    if use_cache:
        brandkit = await _get_offer_brandkit(db, offer_id)
        if brandkit and brandkit.style_anchor_json and cache_key:
            cached = brandkit.style_anchor_json.get(cache_key)
            if cached and isinstance(cached, dict):
                return cached

    posters = await _query_top_reference_posters(db, offer_id, cache_key, limit=3)
    anchor = _aggregate_tags(posters)

    storage = LocalStorageAdapter()
    poster_bytes = await _load_poster_bytes(storage, posters[:2])

    # Layer 1: deterministic color palette (always run)
    palette = _extract_palette(poster_bytes[:1])
    if palette:
        anchor["palette"] = palette

    # Layer 2: vision-LLM description (best-effort)
    if poster_bytes:
        try:
            vision = await _vision_describe(db, poster_bytes)
            if vision:
                anchor.update(vision)
        except Exception as e:
            logger.warning("Vision-LLM style extraction skipped: %s", e)

    if use_cache and cache_key:
        await _save_to_cache(db, offer_id, cache_key, anchor)

    return anchor


async def pick_reference_posters(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID,
    selling_point: str,
    limit: int = 2,
) -> list[Asset]:
    """Public wrapper for the top-N reference poster picker.

    Used by the trust-the-model image-edits path: returns Asset rows so
    the caller can fetch their bytes and pass them as visual references
    directly to the model.
    """
    return await _query_top_reference_posters(
        db, offer_id, (selling_point or "").strip(), limit=limit
    )


async def invalidate_offer_cache(db: AsyncSession, offer_id: uuid.UUID) -> None:
    """Clear the style-anchor cache for an offer. Call after asset uploads /
    deletions so the next render picks up the changed reference set."""
    brandkit = await _get_offer_brandkit(db, offer_id)
    if brandkit and brandkit.style_anchor_json:
        brandkit.style_anchor_json = None
        await db.flush()


def render_style_summary(anchor: dict, brand_voice: str | None = None) -> str:
    """Render the anchor as a compact text fragment for the AI prompt.

    The order is intentional — color palette first (most actionable for
    the diffusion model), then composition, then mood, then everything
    else as supporting context. Subjects are placed last because they
    can lure the model into copying surface elements (logos, QR codes)
    rather than the deeper style signal.
    """
    parts: list[str] = []

    palette = anchor.get("palette") or []
    if palette:
        parts.append("brand color palette: " + ", ".join(palette[:5]))

    composition = anchor.get("composition")
    if composition:
        parts.append("composition: " + str(composition).strip())

    typography = anchor.get("typography_style")
    if typography:
        parts.append("typography aesthetic: " + str(typography).strip())

    lighting = anchor.get("lighting")
    if lighting:
        parts.append("lighting: " + str(lighting).strip())

    vibe = anchor.get("vibe") or (", ".join((anchor.get("moods") or [])[:3]) or None)
    if vibe:
        parts.append("mood: " + str(vibe).strip())

    motifs = anchor.get("visual_motifs") or []
    if motifs:
        parts.append("visual motifs: " + ", ".join([str(m) for m in motifs[:5]]))

    if brand_voice:
        first = brand_voice.split("。")[0].split(".")[0].strip()
        if first and len(first) <= 120:
            parts.append("brand tone: " + first)

    if not parts:
        return "professional clean modern marketing aesthetic"
    return "; ".join(parts)


# ── Reference posters ───────────────────────────────────────────────


async def _get_offer_brandkit(
    db: AsyncSession, offer_id: uuid.UUID
) -> BrandKit | None:
    result = await db.execute(
        select(BrandKit).where(
            BrandKit.scope_type == "offer",
            BrandKit.scope_id == offer_id,
        )
    )
    return result.scalars().first()


async def _query_top_reference_posters(
    db: AsyncSession,
    offer_id: uuid.UUID,
    selling_point: str,
    *,
    limit: int = 3,
) -> list[Asset]:
    """Fetch top-N most reusable image assets matching the selling point.

    Strategy:
      1. Image assets scoped to this offer with parse_status='done'
      2. Skip very-small images (< 600px on either axis) — those are
         logos / icons, not posters; they'd swamp K-means with brand
         primary colors and miss the actual marketing palette.
      3. Filter to those whose tags_json.selling_point list contains
         the term (loose match)
      4. Order by hook_score DESC, then reuse_score DESC
      5. Fallback to "any image with any selling_point tag" when the
         exact match returns 0 rows
    """
    base_q = (
        select(Asset)
        .where(
            Asset.scope_type == "offer",
            Asset.scope_id == offer_id,
            Asset.asset_type == "image",
            Asset.parse_status == "done",
        )
        .order_by(
            Asset.hook_score.desc().nullslast(),
            Asset.reuse_score.desc().nullslast(),
        )
    )
    candidates = (await db.execute(base_q.limit(20))).scalars().all()

    def _is_poster_sized(asset: Asset) -> bool:
        meta = asset.metadata_json or {}
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        return w >= 600 or h >= 600  # cheap "real poster" gate

    poster_candidates = [a for a in candidates if _is_poster_sized(a)]

    matched: list[Asset] = []
    for asset in poster_candidates:
        tags = asset.tags_json or {}
        sp_tags = tags.get("selling_point") or []
        if any(_loose_match(selling_point, sp) for sp in sp_tags if isinstance(sp, str)):
            matched.append(asset)
            if len(matched) >= limit:
                break

    if matched:
        return matched

    # Fallback — any tagged image (better than no anchor at all)
    return [a for a in poster_candidates if (a.tags_json or {}).get("selling_point")][
        :limit
    ]


def _loose_match(query: str, tag: str) -> bool:
    q = (query or "").strip()
    t = (tag or "").strip()
    if not q or not t:
        return False
    if q == t:
        return True
    short = q if len(q) <= len(t) else t
    long = t if len(q) <= len(t) else q
    return short in long


def _aggregate_tags(posters: list[Asset]) -> dict[str, Any]:
    subjects: list[str] = []
    moods: list[str] = []
    forms: list[str] = []
    channels: list[str] = []

    seen: dict[str, set[str]] = {k: set() for k in ("s", "m", "f", "c")}

    def _add(bucket: list[str], key: str, value: str) -> None:
        if isinstance(value, str) and value not in seen[key]:
            seen[key].add(value)
            bucket.append(value)

    for poster in posters:
        tags = poster.tags_json or {}
        for s in tags.get("subject", []) or []:
            _add(subjects, "s", s)
        for m in tags.get("campaign_type", []) or []:
            _add(moods, "m", m)
        for f in tags.get("content_form", []) or []:
            _add(forms, "f", f)
        for c in tags.get("channel_fit", []) or []:
            _add(channels, "c", c)

    return {
        "subjects": subjects[:8],
        "moods": moods[:5],
        "forms": forms,
        "channels": channels,
        "reference_count": len(posters),
    }


async def _load_poster_bytes(
    storage: LocalStorageAdapter, posters: list[Asset]
) -> list[bytes]:
    out: list[bytes] = []
    for poster in posters:
        if not poster.storage_uri:
            continue
        try:
            out.append(await storage.get_file(poster.storage_uri))
        except Exception as e:
            logger.warning("Reference poster load failed (%s): %s", poster.id, e)
    return out


# ── PIL color palette extraction ────────────────────────────────────


def _extract_palette(image_bytes_list: list[bytes]) -> list[str]:
    """K-means quantization → top-K hex codes. Filters near-white and
    near-black so the palette reflects the brand's chromatic colors,
    not the print background."""
    if not image_bytes_list:
        return []
    raw = image_bytes_list[0]
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        logger.warning("Palette extraction failed (image open): %s", e)
        return []

    img.thumbnail((256, 256), Image.LANCZOS)
    # Quantize to 8 colors via PIL's median cut, then read the palette.
    try:
        quant = img.quantize(colors=8, method=Image.Quantize.MEDIANCUT, kmeans=8)
    except Exception:
        try:
            quant = img.quantize(colors=8)
        except Exception as e:
            logger.warning("Palette extraction failed (quantize): %s", e)
            return []

    palette = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], reverse=True)  # [(count, idx), ...]

    hex_codes: list[str] = []
    for count, idx in counts:
        r = palette[idx * 3]
        g = palette[idx * 3 + 1]
        b = palette[idx * 3 + 2]
        # Drop near-white and near-black (background, type ink)
        if r > 240 and g > 240 and b > 240:
            continue
        if r < 20 and g < 20 and b < 20:
            continue
        # Drop near-grey (low chroma) — they read as backdrop, not brand
        chroma = max(r, g, b) - min(r, g, b)
        if chroma < 20:
            continue
        hex_codes.append(f"#{r:02X}{g:02X}{b:02X}")
        if len(hex_codes) >= 5:
            break
    return hex_codes


# ── Vision LLM call ─────────────────────────────────────────────────


# OpenAI's response_format=json_object enforcement requires the literal
# word "json" to appear somewhere in the messages — otherwise it returns
# a 400 ("messages must contain the word 'json'…"). Both the system and
# user halves of the conversation include it explicitly below.
_VISION_SYSTEM_PROMPT = """You are a visual designer extracting style guidance from reference marketing posters for an AI image generator.

Look at the input images and return a single json object with these fields:
  - composition: 1 short sentence describing the layout (where elements live)
  - lighting: short phrase (e.g. "soft studio diffused", "high-key bright")
  - vibe: short phrase describing the emotional tone (e.g. "energetic professional", "aspirational luxury")
  - visual_motifs: array of 3-5 short phrases — recurring visual elements ABOVE THE TYPE (e.g. "soft holographic gradient", "geometric chip frames", "front-facing portrait"). Do NOT list specific text content, logos, or QR codes.
  - typography_style: short phrase about the headline aesthetic (e.g. "bold sans-serif on contrasting band", "calligraphic Chinese with pastel ribbon")

Constraints:
  - Describe STYLE, not CONTENT. Never quote text from the posters.
  - Never name brands, products, people.
  - Keep each phrase under 80 characters.
  - Output ONLY the json object, no surrounding prose."""


async def _vision_describe(
    db: AsyncSession, image_bytes_list: list[bytes]
) -> dict[str, Any] | None:
    """Call the active OpenAI-compatible LLM with vision input. Returns
    the parsed JSON dict on success, ``None`` when no LLM is configured
    or the call fails (caller is expected to log + degrade gracefully).
    """
    llm = await _get_active_openai_llm(db)
    if not llm or not llm.api_key:
        return None

    try:
        from openai import AsyncOpenAI
    except Exception as e:
        logger.warning("openai SDK not available: %s", e)
        return None

    client = AsyncOpenAI(
        api_key=llm.api_key,
        base_url=(llm.base_url or "").strip() or None,
        timeout=60.0,
    )

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "Extract style guidance from these reference posters and return as a json object.",
        }
    ]
    for raw in image_bytes_list[:2]:
        b64 = _shrink_to_jpeg_b64(raw)
        if not b64:
            continue
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    if len(content) < 2:
        return None

    try:
        resp = await client.chat.completions.create(
            model=llm.model_name,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            max_tokens=600,
            temperature=0.4,
        )
    except Exception as e:
        logger.warning("Vision LLM call failed (%s): %s", llm.model_name, e)
        return None

    text = (resp.choices[0].message.content or "").strip()
    return _safe_parse_vision_json(text)


def _shrink_to_jpeg_b64(raw: bytes, *, max_side: int = 768) -> str | None:
    """Resize + JPEG-compress a poster for vision-input efficiency.
    Returns base64 string (no header) or None on failure."""
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    try:
        img.save(out, format="JPEG", quality=82, optimize=True)
    except Exception:
        return None
    return base64.b64encode(out.getvalue()).decode("ascii")


def _safe_parse_vision_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Some models still wrap JSON in a code fence despite
        # response_format=json_object. Strip the fence and retry.
        cleaned = text.strip().strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            data = json.loads(cleaned)
        except Exception:
            logger.warning("Vision LLM returned non-JSON: %s", text[:200])
            return None
    if not isinstance(data, dict):
        return None

    out: dict[str, Any] = {}
    for k in ("composition", "lighting", "vibe", "typography_style"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:200]
    motifs = data.get("visual_motifs")
    if isinstance(motifs, list):
        cleaned_motifs = [
            str(m).strip()[:80]
            for m in motifs
            if isinstance(m, (str, int)) and _is_safe_motif(str(m))
        ]
        if cleaned_motifs:
            out["visual_motifs"] = cleaned_motifs[:6]
    return out


# Surface-element keywords that, when echoed into the image-gen prompt,
# trick the model into RE-DRAWING them on top of the AI background. The
# downstream PIL renderer is the single source of truth for these — we
# never want gpt-image-2 painting a fake QR code or wordmark itself.
_MOTIF_BANNED_SUBSTRINGS = (
    "logo",
    "wordmark",
    "headline",
    "title text",
    "qr",
    "二维码",
    "barcode",
    "watermark",
    "copyright",
    "text label",
    "text overlay",
    "captions",
    "tagline copy",
    "button label",
    "cta button",
    "标题",
)


def _is_safe_motif(s: str) -> bool:
    """Filter motifs that would invite the model to draw surface chrome."""
    if not s:
        return False
    lo = s.lower()
    return not any(bad in lo for bad in _MOTIF_BANNED_SUBSTRINGS)


async def _get_active_openai_llm(db: AsyncSession) -> LLMConfig | None:
    return (
        await db.execute(
            select(LLMConfig).where(
                LLMConfig.provider == "openai",
                LLMConfig.is_active.is_(True),
            )
        )
    ).scalars().first()


# ── Cache writeback ─────────────────────────────────────────────────


async def _save_to_cache(
    db: AsyncSession,
    offer_id: uuid.UUID,
    cache_key: str,
    anchor: dict,
) -> None:
    brandkit = await _get_offer_brandkit(db, offer_id)
    if not brandkit:
        return
    cache = dict(brandkit.style_anchor_json or {})
    cache[cache_key] = anchor
    brandkit.style_anchor_json = cache
    await db.flush()
