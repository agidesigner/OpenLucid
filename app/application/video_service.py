"""Video generation service.

Implements the **lazy on-GET refresh** pattern (no background poller):

  1. POST /creations/{cid}/videos
       → create job row (status=pending)
       → call provider.create_avatar_video synchronously
       → on success: update job (provider_task_id, status=processing, started_at)
       → on failure: update job (status=failed, error_message, finished_at) and re-raise
       → return job

  2. GET /videos/{id}
       → load job
       → if status in {completed, failed}: return as-is
       → if status in {pending, processing}: call provider.get_video_status
                                             → update job
                                             → return refreshed
       → if provider_config_id is null (config was deleted): return as-is

This means the user must poll from the frontend; container restarts are safe;
no background scheduler is needed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.video import CreateVideoRequest, get_video_provider
from app.exceptions import AppError, NotFoundError
from app.infrastructure.media_provider_repo import MediaProviderRepository
from app.infrastructure.video_job_repo import VideoJobRepository
from app.models.creation import Creation
from app.models.video_generation_job import VideoGenerationJob
from app.schemas.video import (
    VideoGenerateRequest,
    VideoJobResponse,
    VideoJobWithCreationResponse,
)

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_ERROR_MESSAGE_RE = __import__("re").compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _extract_clean_cause(raw: str) -> str:
    """Pull the human-readable bit out of a provider error string.

    Provider exceptions surface in a few shapes that all look terrible if
    rendered raw:
      1. Python tuple-repr from ``str(AppError(code, msg, status))`` →
         ``('VEO_SUBMIT_FAILED', "Veo submit returned 400: ...", 502)``
         (quoting flips between ' and " depending on apostrophes inside)
      2. JSON blob embedded in (1)'s message →
         ``Veo submit returned 400: {\\n  "error": {\\n    "message": "...", ...}}``
      3. Plain string (httpx connection errors etc.)

    Tries (1) → (2) → (3) in order. Goal: one concise sentence that
    explains *what went wrong* without leaking JSON braces or Python repr
    quotes into the UI."""
    import ast
    s = raw.strip()
    # 1. Strip tuple-repr — ast.literal_eval handles mixed quoting safely.
    if s.startswith("(") and s.endswith(")"):
        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, tuple):
                # Pick the longest string element — that's the human message
                # (the code is short, status is an int).
                strs = [x for x in parsed if isinstance(x, str)]
                if strs:
                    s = max(strs, key=len)
        except (ValueError, SyntaxError):
            pass
    # 2. Pull the inner JSON ``message`` field if present (Veo / OpenAI / etc.)
    jm = _ERROR_MESSAGE_RE.search(s)
    if jm:
        # Decode JSON-style escapes (\n, \", etc.) so the rendered text reads
        # like a sentence not a literal escape sequence.
        try:
            s = jm.group(1).encode("utf-8").decode("unicode_escape", errors="replace")
        except Exception:
            s = jm.group(1)
    # 3. Trim
    # Don't strip backticks — they're often used as code-quoting around
    # field/parameter names (e.g. ``numberOfVideos`` in Gemini errors) and
    # losing them changes the meaning. Strip only stray surrounding quotes.
    return s.strip().strip("'\"").strip()[:220]


def _classify_broll_error(msg: str) -> str:
    """Add a short human-readable cause prefix in front of the cleaned-up
    provider error so the UI surface (job.error_message + params.broll_warnings)
    tells the user *why* a shot failed without making them parse a stack
    trace. Conservative substring match — when nothing matches, return just
    the cleaned cause."""
    cause = _extract_clean_cause(msg)
    m = (msg + " " + cause).lower()
    if any(s in m for s in ("insufficient", "credit", "balance", "quota", "exceed", "billing", " 402")):
        return f"Insufficient credits / quota — {cause}"
    if any(s in m for s in ("rate limit", "rate-limit", "too many request", " 429")):
        return f"Rate-limited by provider — {cause}"
    if any(s in m for s in ("unauthorized", "invalid_api_key", "authentication", "api key", " 401", " 403")):
        return f"Authentication / permission denied — {cause}"
    if any(s in m for s in ("timeout", "timed out", "deadline")):
        return f"Provider timeout — {cause}"
    return cause


async def _extract_video_cover(local_mp4_path: str) -> str | None:
    """Extract the first frame of a composited video as a cover thumbnail.

    Runs ffmpeg on the local mp4 and writes a JPEG sibling next to it.
    Why: chanjing returns the avatar's portrait as ``cover_url``, but
    that image is just a static head-shot — it doesn't reflect what
    the user actually sees at second 0 of the final video. When B-roll
    inserts at ``insert_after_char=0`` (retention opener), the real
    first frame is the B-roll cutaway, not the avatar. Generating our
    own cover from the composited mp4 makes the Past Videos thumbnail
    match what plays when the user clicks ▶.

    Convention matches asset thumbnails: fit within 512×512 box,
    JPEG ~85, never upscale. Returns the public ``/uploads/...`` URL
    or ``None`` on any failure (caller falls back to chanjing's cover).
    """
    import os
    out_path = local_mp4_path.rsplit(".", 1)[0] + "_cover.jpg"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", local_mp4_path,
            "-frames:v", "1",
            # ``-ss 0`` would land before the first valid frame on some
            # codecs; default (no -ss) takes the first decoded frame.
            "-vf", "scale='min(iw,512)':'min(ih,512)':force_original_aspect_ratio=decrease",
            "-q:v", "3",  # ~JPEG q85
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            return None
        # Translate filesystem path → public URL. The composited dir
        # is already mounted at /uploads/composited (see static mount
        # in app.main); we just rebase the filename.
        from app.config import settings
        rel = os.path.relpath(out_path, str(settings.STORAGE_BASE_PATH))
        return "/uploads/" + rel.replace(os.sep, "/")
    except FileNotFoundError:
        logger.warning("ffmpeg not found, skipping cover extraction for %s", local_mp4_path)
        return None
    except Exception as e:
        logger.warning("Cover extraction failed for %s: %s", local_mp4_path, e)
        return None


def _aspect_label_to_ratio(aspect_ratio: str | None) -> float | None:
    return {
        "portrait": 9 / 16,
        "9:16": 9 / 16,
        "landscape": 16 / 9,
        "16:9": 16 / 9,
        "square": 1.0,
        "1:1": 1.0,
    }.get(aspect_ratio or "")


def _derive_first_frame_reference_image(
    file_bytes: bytes,
    file_name: str,
    source_width: int,
    source_height: int,
    target_aspect: str | None,
    min_aspect: float | None,
    max_aspect: float | None,
) -> tuple[bytes, str, dict]:
    """Create a provider-safe first-frame image without mutating the asset.

    Long poster assets are valuable in the knowledge base as-is, but
    Chanjing/Doubao reject first-frame images outside [0.5, 2.0]. For B-roll
    submission only, crop a temporary top-center frame to the target video
    aspect (9:16 / 16:9 / 1:1), clamped into the provider's accepted window.
    """
    import io
    from pathlib import Path

    from PIL import Image, ImageOps

    src_ratio = source_width / source_height if source_height else 1.0
    wanted = _aspect_label_to_ratio(target_aspect) or src_ratio
    if min_aspect is not None:
        wanted = max(wanted, float(min_aspect))
    if max_aspect is not None:
        wanted = min(wanted, float(max_aspect))

    with Image.open(io.BytesIO(file_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        width, height = im.size
        current = width / height if height else wanted

        if current < wanted:
            crop_h = max(1, min(height, round(width / wanted)))
            crop_w = width
            left = 0
            top = 0
        elif current > wanted:
            crop_w = max(1, min(width, round(height * wanted)))
            crop_h = height
            left = max(0, (width - crop_w) // 2)
            top = 0
        else:
            crop_w, crop_h, left, top = width, height, 0, 0

        cropped = im.crop((left, top, left + crop_w, top + crop_h))
        if cropped.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", cropped.size, (255, 255, 255))
            if cropped.mode == "P":
                cropped = cropped.convert("RGBA")
            bg.paste(cropped, mask=cropped.split()[-1] if cropped.mode in ("RGBA", "LA") else None)
            cropped = bg
        elif cropped.mode != "RGB":
            cropped = cropped.convert("RGB")

        # Provider upload does not need original poster resolution. Cap
        # temporary references to 1536px on the long edge to keep upload
        # size predictable while preserving enough detail for i2v.
        cropped.thumbnail((1536, 1536), Image.LANCZOS)
        out = io.BytesIO()
        cropped.save(out, "JPEG", quality=90, optimize=True)

    stem = Path(file_name or "first_frame").stem or "first_frame"
    derived_name = f"{stem}_broll_ref.jpg"
    info = {
        "source_width": source_width,
        "source_height": source_height,
        "source_aspect": round(src_ratio, 4),
        "target_aspect": round(wanted, 4),
        "crop_box": [left, top, left + crop_w, top + crop_h],
        "derived_width": cropped.size[0],
        "derived_height": cropped.size[1],
    }
    return out.getvalue(), derived_name, info


def _to_response(job: VideoGenerationJob) -> VideoJobResponse:
    return VideoJobResponse(
        id=str(job.id),
        creation_id=str(job.creation_id),
        provider=job.provider,
        provider_config_id=str(job.provider_config_id) if job.provider_config_id else None,
        provider_task_id=job.provider_task_id,
        status=job.status,  # type: ignore[arg-type]
        params=job.params or {},
        video_url=job.video_url,
        cover_url=job.cover_url,
        duration_seconds=job.duration_seconds,
        progress=job.progress,
        error_message=job.error_message,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        created_at=job.created_at.isoformat() if job.created_at else "",
        updated_at=job.updated_at.isoformat() if job.updated_at else "",
    )


# ── Create ──────────────────────────────────────────────────────────


async def create_video_job(
    db: AsyncSession,
    creation_id: uuid.UUID,
    data: VideoGenerateRequest,
) -> VideoJobResponse:
    """Create a video generation job and submit it to the provider synchronously."""
    # 1. Verify creation exists
    creation = await db.get(Creation, creation_id)
    if not creation:
        raise NotFoundError("Creation", str(creation_id))

    # 2. Look up provider config
    try:
        config_uuid = uuid.UUID(data.provider_config_id)
    except (ValueError, TypeError) as e:
        raise AppError("INVALID_PROVIDER_CONFIG_ID", "Invalid provider_config_id", 400) from e

    mp_repo = MediaProviderRepository(db)
    provider_config = await mp_repo.get_by_id(config_uuid)
    if not provider_config:
        raise NotFoundError("MediaProviderConfig", data.provider_config_id)

    # Style preset values flow straight through. We previously
    # auto-injected the offer's BrandKit primary / secondary as the
    # subtitle fill / stroke when the user hadn't overridden — the
    # intent was "brand-aware subtitles for free", but in practice it
    # silently defeated every style choice ("I picked Classic, why is
    # the subtitle red?"). Brand-color matching is now opt-in: users
    # who want it click "自定义颜色" and pick the hex explicitly.
    effective_subtitle_color = data.subtitle_color
    effective_subtitle_stroke = data.subtitle_stroke

    # 3. Build the params we'll send (and store on the row for debug)
    params: dict = {
        "avatar_id": data.avatar_id,
        "voice_id": data.voice_id,
        "script": data.script,
        "aspect_ratio": data.aspect_ratio,
        "caption": data.caption,
        # Persist the user-picked subtitle style so the B-roll compositor
        # (which runs later, after polling) can reproduce the same
        # typography as the avatar provider burns in. Previously the
        # compositor defaulted to hardcoded white/black/36px, producing
        # subtitles that looked nothing like the avatar's chosen style.
        "subtitle_style": data.subtitle_style,
        "subtitle_color": effective_subtitle_color,
        "subtitle_stroke": effective_subtitle_stroke,
        "broll": data.broll,
        "name": data.name,
        "provider_extras": data.provider_extras or {},
    }

    # 4. Insert the job row in pending state
    job_repo = VideoJobRepository(db)
    job = await job_repo.create(
        creation_id=creation_id,
        provider=provider_config.provider,
        provider_config_id=provider_config.id,
        status="pending",
        params=params,
    )
    await db.commit()
    await db.refresh(job)

    # 5. Call provider synchronously to submit the task
    video_provider = get_video_provider(
        provider_config.provider, provider_config.credentials or {}
    )
    # Use the brandkit-resolved colors (not raw ``data``) so the avatar
    # provider burns subtitles in the same palette the compositor will use.
    create_req = CreateVideoRequest(
        avatar_id=data.avatar_id,
        voice_id=data.voice_id,
        script=data.script,
        aspect_ratio=data.aspect_ratio,
        caption=data.caption,
        subtitle_style=data.subtitle_style,
        subtitle_color=effective_subtitle_color,
        subtitle_stroke=effective_subtitle_stroke,
        name=data.name,
        provider_extras=data.provider_extras or {},
    )
    # NOTE: ``create_avatar_video`` is intentionally NOT called here. We
    # submit B-roll FIRST (below) so that if the B-roll provider rejects
    # every shot at submit time (auth/quota/rate-limit), we can abort the
    # whole job before burning credits on the avatar provider. The actual
    # avatar submit happens after the B-roll block, gated by its result.

    # 5. If B-roll requested, use the AI-director's broll_plan from structured_content.
    #    The LLM already decided WHERE and WHY to insert B-roll when writing the script.
    #    We just submit the generation tasks for each planned insert point.
    #
    #    B-roll provider is RESOLVED INDEPENDENTLY from the avatar provider via
    #    MediaCapabilityDefault(video_gen). This lets users mix providers — e.g.
    #    Jogg for the talking-avatar track and Chanjing/Veo for the B-roll clips.
    if data.broll and creation.structured_content:
        sc = creation.structured_content
        # Prefer the caller's overridden plan — the UI lets users edit
        # prompts and add shots before hitting Generate. Fall back to the
        # AI-director's stored plan when the caller didn't override.
        broll_plan = data.broll_plan if data.broll_plan is not None else (sc.get("broll_plan") or [])

        # Defensive coerce: the LLM sometimes writes a Chinese sentence
        # (the narration excerpt it wants to cut over) into
        # ``insert_after_char`` instead of an integer offset. The
        # compositor would then collapse all offending shots to 0.0s and
        # drop them as duplicates — B-roll silently disappears. When we
        # see a non-int, try to locate that string inside the concatenated
        # narration and use the matched char offset. If still unresolvable,
        # drop that entry with a warning rather than let it mask-out at 0.
        sections_for_coerce = sc.get("sections") or {}
        section_order_for_coerce = sc.get("section_ids") or list(sections_for_coerce.keys())
        full_narration = "".join(
            (sections_for_coerce.get(sid) or {}).get("text", "")
            for sid in section_order_for_coerce
        )
        coerced_plan: list[dict] = []
        for idx, entry in enumerate(broll_plan):
            pos = entry.get("insert_after_char", 0)
            if isinstance(pos, int):
                coerced_plan.append(entry)
                continue
            if isinstance(pos, str) and pos.strip() and full_narration:
                # Match against the narration. LLM often writes the full
                # sentence it wants the cut to land after, so we locate
                # the substring and use the end of the match.
                needle = pos.strip()
                match_idx = full_narration.find(needle)
                if match_idx >= 0:
                    fixed = {**entry, "insert_after_char": match_idx + len(needle)}
                    coerced_plan.append(fixed)
                    logger.info(
                        "B-roll #%d: coerced string insert_after_char to %d (matched narration)",
                        idx, fixed["insert_after_char"],
                    )
                    continue
            # Unresolvable — skip rather than collapse to 0.
            logger.warning(
                "B-roll #%d dropped: insert_after_char=%r is not an int and doesn't match narration",
                idx, pos,
            )
        broll_plan = coerced_plan
        section_order = sc.get("section_ids")
        if not section_order:
            _STRUCTURE_ORDERS = {
                "hook_body_cta": ["hook", "body", "cta"],
                "pas": ["problem", "agitate", "solve"],
                "before_after_bridge": ["before", "after", "bridge"],
                "story_lesson_cta": ["story", "lesson", "cta"],
            }
            section_order = _STRUCTURE_ORDERS.get(sc.get("structure_id", "")) or list((sc.get("sections") or {}).keys())
        sections = sc.get("sections") or {}
        aspect_map = {"portrait": "9:16", "landscape": "16:9", "square": "1:1"}
        ar = aspect_map.get(data.aspect_ratio, "9:16")

        if not broll_plan:
            logger.info("B-roll requested but no broll_plan in structured_content — skipping")
        else:
            # Resolve an independent B-roll provider (avatar provider may be Jogg,
            # but Jogg doesn't expose submit_broll_clip — use video_gen default instead).
            # Precedence: per-request override → MediaCapabilityDefault(video_gen) → fallback.
            broll_provider_config = None
            broll_model_code = None
            try:
                if data.broll_provider_config_id and data.broll_model_code:
                    override_uuid = uuid.UUID(data.broll_provider_config_id)
                    broll_provider_config = await mp_repo.get_by_id(override_uuid)
                    if broll_provider_config:
                        broll_model_code = data.broll_model_code
                        logger.info(
                            "B-roll: per-request override provider=%s model=%s (avatar=%s)",
                            broll_provider_config.provider, broll_model_code, provider_config.provider,
                        )
                    else:
                        logger.info(
                            "B-roll: override provider_config_id %s missing — falling back to default",
                            data.broll_provider_config_id,
                        )
                if broll_provider_config is None:
                    from app.models.media_capability_default import MediaCapabilityDefault
                    cap_result = await db.execute(
                        select(MediaCapabilityDefault).where(MediaCapabilityDefault.capability == "video_gen")
                    )
                    cap = cap_result.scalar_one_or_none()
                    if cap and cap.provider_config_id:
                        broll_provider_config = await mp_repo.get_by_id(cap.provider_config_id)
                        if broll_provider_config:
                            broll_model_code = cap.model_code or None
                            logger.info(
                                "B-roll: using video_gen default provider=%s model=%s (avatar=%s)",
                                broll_provider_config.provider, broll_model_code, provider_config.provider,
                            )
                        else:
                            logger.info("B-roll: video_gen default provider_config_id points at a missing row")
            except Exception as e:
                logger.warning("B-roll: failed to resolve B-roll provider: %s", e)

            # If no independent B-roll provider but the avatar provider itself can do
            # B-roll (e.g. Chanjing avatar + Chanjing ai_creation), fall back to that.
            broll_video_provider = None
            if broll_provider_config:
                broll_video_provider = get_video_provider(
                    broll_provider_config.provider, broll_provider_config.credentials or {}
                )
            elif hasattr(video_provider, "submit_broll_clip"):
                broll_provider_config = provider_config
                broll_video_provider = video_provider
                logger.info("B-roll: no video_gen default, using avatar provider as fallback")

            if not broll_video_provider:
                logger.info("B-roll: no B-roll-capable provider configured — skipping")
            else:
                # Per-shot references only — the previous auto-load of
                # offer-KB images as ``style_references`` for every shot
                # was a no-op in practice: chanjing relay and Veo both
                # drop ``style_references`` silently (no upstream channel),
                # so the only effect was wasting bandwidth + chanjing
                # tokens uploading images that were then discarded. Per-
                # shot ``first_frame`` / ``reference`` modes are now
                # capability-gated in the UI and routed below.
                from app.adapters.storage import LocalStorageAdapter
                storage = LocalStorageAdapter()

                # Cap aligned with composer spec (up to 5 inserts for 90s+).
                # Build specs synchronously (cheap prep), then fan out the
                # provider calls with a bounded semaphore. Previously this
                # loop awaited each ``submit_broll_clip`` serially; for 3-5
                # broll clips at ~5-30s each, total time dominated the job
                # submission latency. Parallelism cuts that roughly in half
                # (ceil(N / BROLL_SUBMIT_CONCURRENCY) waves instead of N).
                broll_specs: list[tuple[int, dict, str, int, dict]] = []
                # Direct-use entries: per-shot user assets that bypass AI
                # generation entirely. They share the broll_tasks list with
                # AI tasks (so the poll path picks both up uniformly), but
                # carry ``source="asset"`` + ``asset_id`` instead of a
                # ``task_id``. Poll phase resolves asset_id → local path
                # at composite time (we deliberately don't store the path
                # in params — it's exposed via the jobs API and would
                # leak server FS layout).
                direct_broll_tasks: list[dict] = []
                # Hoisted up from below so the direct/reference branches
                # in the loop can record submit-time failures.
                broll_failures: list[dict] = []
                from app.application.broll_matching_service import (
                    get_asset_url_for_broll,
                )
                from app.adapters.video.base import (
                    FirstFrame,
                    StyleReference,
                )

                for idx, entry in enumerate(broll_plan[:5]):
                    prompt = (entry.get("prompt") or "").strip()
                    if not prompt:
                        continue
                    shot_type = entry.get("type", "illustrative")
                    asset_id_raw = entry.get("asset_id")
                    # "direct" | "first_frame" | "reference" | None.
                    # Legacy data may still carry "reference"; that path stays
                    # compatible but will be a no-op for providers without a
                    # native style_references channel (chanjing/veo today).
                    asset_mode = entry.get("asset_mode")

                    # Retention opener: prepend style cues that nudge Seedance
                    # toward a stopping-power shot. Without this, retention
                    # and illustrative shots look identical — which defeats
                    # the whole point of the type distinction.
                    # Skip the prefix in direct mode — the asset is the
                    # opener, not an AI prompt.
                    if shot_type == "retention" and asset_mode != "direct":
                        # Match prefix language to prompt language so the LLM
                        # sees a coherent single-language prompt (previously
                        # an English prefix was prepended even to Chinese
                        # prompts, creating bilingual noise).
                        # Can't use ``detect_text_language`` here — its
                        # 30-char minimum sample throws out short broll
                        # prompts and returns None, which would default to
                        # English even for clearly-Chinese inputs. "Any
                        # CJK char present" is the right granularity.
                        has_cjk = any("一" <= c <= "鿿" for c in prompt)
                        retention_prefix = (
                            "特写推进镜头，0.75x 慢动作，浅景深，电影级打光，"
                            "留人开场冲击力。"
                            if has_cjk else
                            "Extreme close-up push-in, slow-motion 0.75x, "
                            "shallow depth of field, cinematic lighting, "
                            "visually striking opener that stops the scroll. "
                        )
                        prompt = retention_prefix + prompt
                    dur = max(5, min(entry.get("duration_seconds") or 5, 10))

                    # ── Direct-use mode: skip AI submit, mark this shot
                    # as asset-backed (source="asset" + asset_id). Poll
                    # phase resolves the asset to a local file path on
                    # the fly and feeds it to broll_clips alongside
                    # AI-generated clips, no provider call required.
                    if asset_id_raw and asset_mode == "direct":
                        try:
                            asset_uri, asset_meta = await get_asset_url_for_broll(
                                db, uuid.UUID(str(asset_id_raw)),
                                expected_aspect=ar,
                            )
                            # Direct mode means "splice the asset literally
                            # into the timeline" — the compositor runs
                            # ffprobe + ffmpeg on the file expecting a
                            # video stream. An image has no duration and
                            # the compositor's segment builder doesn't
                            # use ``-loop 1``, so an image-as-direct
                            # silently drops out (or crashes ``concat -c
                            # copy`` if a sibling segment differs in
                            # stream layout). Reject early with a clear
                            # error so the user can switch to
                            # ``first_frame`` mode (i2v from the image)
                            # or pick a video asset instead. Frontend
                            # should already gate this; backend defends
                            # against MCP / API clients that bypass the UI.
                            if asset_meta.get("asset_type") == "image":
                                raise AppError(
                                    "BROLL_IMAGE_DIRECT_UNSUPPORTED",
                                    f"Asset {asset_meta.get('file_name', '')} is an "
                                    "image; direct splice supports videos only. "
                                    "Use 'first_frame' mode (image-to-video) "
                                    "or pick a video asset.",
                                    400,
                                )
                            # Resolve the asset to its absolute local path
                            # for the compositor to read directly. We
                            # deliberately do NOT call the broll provider's
                            # ``upload_temp_file`` here:
                            #   (a) the asset is already in OpenLucid's
                            #       storage — no reason to round-trip a
                            #       multi-MB file through a third-party CDN;
                            #   (b) Google/Veo's upload_temp_file returns
                            #       a base64 data URI which the compositor's
                            #       httpx-based ``_download`` can't fetch;
                            #   (c) provider uploads default to image MIME
                            #       types and would mislabel a video file.
                            # Submit-time validation only: confirm the
                            # asset's file actually exists on disk so we
                            # fail-fast (rather than mid-composite). The
                            # absolute path is intentionally NOT stored in
                            # broll_tasks — params is exposed via the jobs
                            # API and we don't want to leak server FS layout.
                            # The poll path resolves asset_id → abs path
                            # again at composite time, which has the bonus
                            # of detecting "asset deleted between submit
                            # and composite" and skipping that one shot
                            # instead of crashing the whole job.
                            import os
                            _validation_path = storage.get_absolute_path(asset_uri)
                            if not os.path.exists(_validation_path):
                                raise FileNotFoundError(
                                    f"Asset file missing on disk: {_validation_path}"
                                )
                            # Asset's actual duration may differ from the
                            # planner's request — for direct use, prefer the
                            # asset's real duration (compositor will clip if
                            # over). 5s default if missing.
                            asset_dur_ms = asset_meta.get("duration_ms")
                            asset_dur = int((asset_dur_ms or 5000) / 1000)
                            asset_dur = max(2, min(asset_dur, 15))
                            direct_broll_tasks.append({
                                "index": idx,
                                "asset_id": str(asset_id_raw),
                                # No asset_url — poll phase resolves
                                # asset_id → path on the fly.
                                "type": entry.get("type", "illustrative"),
                                "insert_after_char": entry.get("insert_after_char", 0),
                                "duration_seconds": asset_dur,
                                "prompt": prompt,
                                "source": "asset",
                                # Pass-through to compositor: when True, the
                                # final video keeps the asset's own audio
                                # (silencing TTS for this segment); default
                                # False means strip asset audio and let
                                # avatar narration play continuously.
                                "asset_audio": bool(entry.get("asset_audio")),
                            })
                            logger.info(
                                "B-roll #%d direct-use asset=%s dur=%ds",
                                idx, asset_id_raw, asset_dur,
                            )
                        except Exception as e:
                            err_str = str(e) or e.__class__.__name__
                            logger.warning(
                                "B-roll #%d direct-use failed (asset=%s): %s",
                                idx, asset_id_raw, e,
                            )
                            broll_failures.append({
                                "idx": idx,
                                "prompt": prompt[:80],
                                "error": _classify_broll_error(err_str),
                            })
                        continue  # Skip AI submit for this shot.

                    # ── AI generation modes with optional per-shot reference.
                    # Two reference channels are independent (see
                    # MediaCapabilityOption.supports_first_frame /
                    # supports_style_references):
                    #   * first_frame — hard i2v anchor (chanjing i2v / veo)
                    #   * reference   — soft style hint (no provider today)
                    #
                    # Prep is split into three explicit phases so any
                    # failure in any phase is caught, recorded in
                    # ``broll_failures``, and skips the AI submit for
                    # this shot. Earlier we wrapped all three in one
                    # try/except that swallowed everything to a warning
                    # and still appended to ``broll_specs`` with
                    # first_frame_obj=None — the user's picked asset
                    # got silently ignored and the strict gate at the
                    # bottom couldn't see the failure (because no
                    # broll_failures entry was added). Per product
                    # policy "any broll failure aborts avatar gen",
                    # prep failures must surface like submit failures.
                    first_frame_obj: FirstFrame | None = None
                    style_refs_list: list[StyleReference] = []
                    first_frame_crop: dict | None = None
                    if asset_id_raw and asset_mode in ("first_frame", "reference"):
                        # Phase 1: resolve asset metadata. No
                        # ``expected_aspect`` here — first_frame /
                        # reference feed the AI generator (not the
                        # timeline compositor), so aspect mismatch is
                        # not a hard error; the model handles framing.
                        try:
                            asset_uri, asset_meta = await get_asset_url_for_broll(
                                db, uuid.UUID(str(asset_id_raw)),
                            )
                        except Exception as e:
                            err_str = str(e) or e.__class__.__name__
                            logger.warning(
                                "B-roll #%d %s asset resolve failed (asset=%s): %s",
                                idx, asset_mode, asset_id_raw, e,
                            )
                            broll_failures.append({
                                "idx": idx,
                                "prompt": prompt[:80],
                                "error": _classify_broll_error(err_str),
                            })
                            continue  # skip submit; strict gate sees the failure

                        # Phase 2: type validation. first_frame is
                        # image-only at the provider API level:
                        # chanjing's create_upload_url rejects mp4 with
                        # code=50000 ("only png/jpeg/jpg/heic"); veo's
                        # instance.image only takes base64 image bytes.
                        # Frontend chip strip already prevents this
                        # (video shows only 'direct' chip), but a stale
                        # broll_plan or MCP/API caller could still send
                        # video+first_frame. Reject hard so the user
                        # sees a clear error instead of a silent
                        # text-only fallback. ``reference`` channel
                        # has no live provider yet so we leave its
                        # type rules permissive until we wire the
                        # first real consumer.
                        if asset_mode == "first_frame" and asset_meta.get("asset_type") != "image":
                            err_str = (
                                f"first_frame requires image asset; got "
                                f"{asset_meta.get('asset_type')} "
                                f"({asset_meta.get('file_name', '')})"
                            )
                            logger.warning("B-roll #%d %s", idx, err_str)
                            broll_failures.append({
                                "idx": idx,
                                "prompt": prompt[:80],
                                "error": _classify_broll_error(
                                    "BROLL_FIRST_FRAME_REQUIRES_IMAGE: " + err_str,
                                ),
                            })
                            continue

                        # Phase 2.5: aspect-range check for first_frame.
                        # chanjing/Doubao reject aspect outside [0.5, 2.0]
                        # with code=50000; veo is more forgiving but still
                        # has practical limits. Caps come from the same
                        # registry the picker UI uses, so backend and
                        # frontend agree on the boundary. Defense-in-
                        # depth: even when the picker filters correctly,
                        # an MCP/API caller could still submit a long
                        # poster image.
                        if asset_mode == "first_frame":
                            from app.application.setting_service import _video_model_ref_caps
                            caps = _video_model_ref_caps(
                                broll_provider_config.provider,
                                broll_model_code or "",
                            )
                            amin = caps.get("first_frame_aspect_min")
                            amax = caps.get("first_frame_aspect_max")
                            w = asset_meta.get("width")
                            h = asset_meta.get("height")
                            # Belt-and-braces guard: if the model has a
                            # documented aspect window but the asset
                            # record is missing dimensions (parse pipeline
                            # crashed mid-extraction, or the file slipped
                            # in via an external API), refuse the submit
                            # rather than letting the chanjing API reject
                            # it. Frontend picker also locks these via
                            # ``assetLockReason='metadata_missing'``;
                            # this is the API-level defense for non-UI
                            # callers (MCP tools, CLI).
                            if amin is not None and amax is not None and (not w or not h):
                                err_str = (
                                    f"image asset {asset_meta.get('file_name', '')} "
                                    f"is missing width/height — re-upload to "
                                    f"trigger parse, or pick a different image"
                                )
                                logger.warning("B-roll #%d %s", idx, err_str)
                                broll_failures.append({
                                    "idx": idx,
                                    "prompt": prompt[:80],
                                    "error": _classify_broll_error(
                                        "BROLL_FIRST_FRAME_METADATA_MISSING: " + err_str,
                                    ),
                                })
                                continue
                            if amin and amax and w and h and h > 0:
                                # Local name — must NOT shadow the
                                # outer string ``ar`` (set above to
                                # "9:16" / "16:9" / "1:1") that gets
                                # passed to provider.submit_broll_clip
                                # as ``aspect_ratio``. Using ``ar``
                                # here once leaked the image's float
                                # aspect into the chanjing payload,
                                # which rejected it as code=400 "参数
                                # 无效" because the field expects a
                                # canonical aspect-ratio string.
                                img_ar = w / h
                                if img_ar < amin or img_ar > amax:
                                    # Do not mutate the user's knowledge-base
                                    # asset. Generate a temporary provider-safe
                                    # first-frame image below, using a top-center
                                    # crop to the target video aspect. This keeps
                                    # long posters reusable while avoiding the
                                    # upstream [0.5, 2.0] rejection.
                                    first_frame_crop = {
                                        "source_width": w,
                                        "source_height": h,
                                        "source_aspect": round(img_ar, 4),
                                        "min_aspect": amin,
                                        "max_aspect": amax,
                                    }

                        # Phase 3: upload to provider's temp file
                        # service. Network / quota / auth issues land
                        # here. Pre-strict-gate this was a soft fail
                        # (warn + continue with text-only AI); under
                        # the new policy the user-explicit broll
                        # opt-in means any prep failure is a hard
                        # failure surfaced via the strict gate.
                        try:
                            file_bytes = await storage.get_file(asset_uri)
                            upload_name = asset_meta.get("file_name") or "ref.png"
                            if asset_mode == "first_frame" and first_frame_crop:
                                file_bytes, upload_name, crop_info = _derive_first_frame_reference_image(
                                    file_bytes=file_bytes,
                                    file_name=upload_name,
                                    source_width=int(asset_meta.get("width") or 0),
                                    source_height=int(asset_meta.get("height") or 0),
                                    target_aspect=ar,
                                    min_aspect=first_frame_crop.get("min_aspect"),
                                    max_aspect=first_frame_crop.get("max_aspect"),
                                )
                                first_frame_crop.update(crop_info)
                                logger.info(
                                    "B-roll #%d first_frame auto-cropped asset=%s "
                                    "%sx%s → %sx%s crop=%s",
                                    idx,
                                    asset_id_raw,
                                    first_frame_crop.get("source_width"),
                                    first_frame_crop.get("source_height"),
                                    first_frame_crop.get("derived_width"),
                                    first_frame_crop.get("derived_height"),
                                    first_frame_crop.get("crop_box"),
                                )
                            ref_url = await broll_video_provider.upload_temp_file(
                                file_bytes,
                                upload_name,
                            )
                            if asset_mode == "first_frame":
                                first_frame_obj = FirstFrame(url=ref_url)
                            else:
                                style_refs_list.append(StyleReference(url=ref_url))
                            logger.info(
                                "B-roll #%d %s-mode asset=%s",
                                idx, asset_mode, asset_id_raw,
                            )
                        except Exception as e:
                            err_str = str(e) or e.__class__.__name__
                            logger.warning(
                                "B-roll #%d %s upload failed (asset=%s): %s",
                                idx, asset_mode, asset_id_raw, e,
                            )
                            broll_failures.append({
                                "idx": idx,
                                "prompt": prompt[:80],
                                "error": _classify_broll_error(err_str),
                            })
                            continue

                    submit_kwargs: dict = dict(
                        prompt=prompt, duration=dur,
                        aspect_ratio=ar,
                        style_references=style_refs_list or None,
                        first_frame=first_frame_obj,
                    )
                    if first_frame_crop:
                        submit_kwargs["_reference_crop"] = first_frame_crop
                    if broll_model_code:
                        submit_kwargs["model_code"] = broll_model_code
                    broll_specs.append((idx, entry, prompt, dur, submit_kwargs))

                # Concurrency cap: 3 keeps us well under Jogg/Chanjing per-key
                # rate limits (observed ~5 requests/sec ceilings in testing)
                # while still cutting total submit time by ~50% for a typical
                # 3-5 broll batch. Tune down if a provider starts 429-ing.
                BROLL_SUBMIT_CONCURRENCY = 3
                _sem = asyncio.Semaphore(BROLL_SUBMIT_CONCURRENCY)

                # ``broll_failures`` is hoisted above the spec-building
                # loop so direct/reference branches can append to it.
                # Single-thread asyncio means list.append is safe here.

                async def _submit_one(
                    idx: int, entry: dict, prompt: str, dur: int, submit_kwargs: dict
                ) -> dict | None:
                    async with _sem:
                        reference_crop = submit_kwargs.pop("_reference_crop", None)
                        try:
                            task_id = await broll_video_provider.submit_broll_clip(**submit_kwargs)
                        except Exception as e:
                            err_str = str(e) or e.__class__.__name__
                            logger.warning("B-roll #%d submit failed: %s", idx, e)
                            broll_failures.append({
                                "idx": idx,
                                "prompt": prompt[:80],
                                "error": _classify_broll_error(err_str),
                            })
                            return None
                        logger.info(
                            "B-roll #%d submitted via %s: type=%s char=%s task=%s",
                            idx, broll_provider_config.provider, entry.get("type"),
                            entry.get("insert_after_char"), task_id,
                        )
                        task = {
                            "index": idx,
                            "task_id": task_id,
                            "type": entry.get("type", "illustrative"),
                            "insert_after_char": entry.get("insert_after_char", 0),
                            "duration_seconds": dur,
                            "prompt": prompt,
                        }
                        if reference_crop:
                            task["reference_crop"] = reference_crop
                        return task

                results = await asyncio.gather(
                    *(_submit_one(*s) for s in broll_specs)
                )
                # ``gather`` preserves argument order. Merge AI-submitted
                # tasks with direct-use asset entries, then sort by index
                # so downstream poll/composite see them in narration order.
                ai_broll_tasks = [r for r in results if r is not None]
                broll_tasks: list[dict] = sorted(
                    ai_broll_tasks + direct_broll_tasks,
                    key=lambda t: t.get("index", 0),
                )

                if broll_tasks:
                    params["broll_tasks"] = broll_tasks
                    params["broll_section_order"] = section_order
                    params["broll_sections"] = {sid: dict(sections.get(sid, {})) for sid in section_order}
                    params["broll_mode"] = "cutaway"
                    # Remember which provider to poll for the B-roll tasks — may differ from avatar provider
                    params["broll_provider_config_id"] = str(broll_provider_config.id)
                    job.params = params

                # Surface broll submit failures on the job for the UI's
                # warnings panel. The strict gate below decides whether
                # the job continues; this block only persists the
                # structured warning details so the failed-job row can
                # render per-shot reasons.
                if broll_failures:
                    params["broll_warnings"] = broll_failures
                    params["broll_warning_provider"] = broll_provider_config.provider
                    # ``broll_status`` is set authoritatively by the
                    # strict gate below (always ``failed_strict`` when
                    # any failure exists, since strict policy is
                    # "abort avatar on any broll failure"). The legacy
                    # partial / all_failed values are no longer used —
                    # the gate overwrites them. Don't write them here.
                    if not broll_tasks:
                        # All shots failed → seed ``error_message`` so
                        # the strict gate's overwrite has an existing
                        # value to either reuse or replace; also helps
                        # debug logs read in chronological order.
                        first = broll_failures[0].get("error", "?")
                        job.error_message = (
                            f"B-roll generation failed for all {len(broll_failures)} shot(s) "
                            f"via {broll_provider_config.provider}: {first}"
                        )
                    job.params = params

                # Strict gate: ANY B-roll failure aborts avatar
                # generation. Per product policy: B-roll is no longer
                # treated as "additive enhancement" — if the user
                # explicitly enabled B-roll, they expect a complete
                # video. A partial result (avatar narration without the
                # planned cutaways, or with one shot missing) is a
                # quality regression they didn't ask for.
                #
                # We're inside the broll generation block so reaching
                # this point already implies the user opted into broll;
                # any non-empty ``broll_failures`` is sufficient to
                # bail. The pre-v1.4.5 condition (``broll_specs and
                # broll_failures``) only caught failures from the AI
                # submit phase, missing failures from:
                #   * direct-mode prep (asset deleted between pick
                #     and submit) — broll_specs would be empty if all
                #     shots were direct
                #   * first_frame / reference prep (asset resolve,
                #     type mismatch, upload failure) — those used to
                #     swallow into a warning then fall through to
                #     text-only submit, leaving broll_failures empty
                #     even though the user's picked asset was lost.
                # Both classes of prep failure now append to
                # broll_failures + ``continue``, so a single condition
                # covers everything.
                #
                # Caveat: AI shots already accepted by the provider
                # (in ``broll_tasks``) keep generating server-side and
                # consume credits — neither chanjing nor jogg expose
                # a cancel API. We surface this in the error message
                # so the user understands why the credit ledger moved.
                if broll_failures:
                    succeeded_count = len(broll_tasks)
                    failed_count = len(broll_failures)
                    total = succeeded_count + failed_count
                    first = broll_failures[0].get("error", "?")
                    suffix = ""
                    if succeeded_count > 0:
                        suffix = (
                            f" 提示：已有 {succeeded_count} 个 broll 任务被提供商接受、"
                            f"会继续在服务端生成并消耗 credits（暂无取消接口）。"
                        )
                    job.status = "failed"
                    job.error_message = (
                        f"B-roll 生成失败：{failed_count}/{total} 个分镜在 "
                        f"{broll_provider_config.provider} 上报错（首个错误：{first}）。"
                        f"为避免输出不完整的视频，已跳过数字人合成。{suffix}"
                    )
                    params["broll_status"] = "failed_strict"
                    job.params = params
                    job.finished_at = _utcnow()
                    await db.commit()
                    await db.refresh(job)
                    return _to_response(job)

    # 6. Submit the avatar video — only reached when B-roll was either not
    # requested, partially succeeded, or wasn't attempted (no plan / no
    # broll-capable provider). All-failures bailed out above.
    try:
        provider_task_id = await video_provider.create_avatar_video(create_req)
    except Exception as e:
        logger.warning("create_avatar_video failed for job %s: %s", job.id, e)
        job.status = "failed"
        job.error_message = str(e)[:1000]
        job.finished_at = _utcnow()
        await db.commit()
        await db.refresh(job)
        raise

    job.provider_task_id = provider_task_id
    job.status = "processing"
    job.started_at = _utcnow()

    await db.commit()
    await db.refresh(job)
    return _to_response(job)


# ── Read with lazy refresh ──────────────────────────────────────────


async def get_video_job(
    db: AsyncSession, job_id: uuid.UUID
) -> VideoJobResponse:
    """Return the job, refreshing from the provider if status is non-terminal."""
    job_repo = VideoJobRepository(db)
    job = await job_repo.get_by_id(job_id)
    if not job:
        raise NotFoundError("VideoGenerationJob", str(job_id))

    refreshed = await _maybe_sync_status(db, job)
    return _to_response(refreshed)


async def list_video_jobs_for_creation(
    db: AsyncSession, creation_id: uuid.UUID
) -> list[VideoJobResponse]:
    """List all jobs for a creation. Refreshes any non-terminal ones."""
    creation = await db.get(Creation, creation_id)
    if not creation:
        raise NotFoundError("Creation", str(creation_id))

    job_repo = VideoJobRepository(db)
    jobs = await job_repo.list_for_creation(creation_id)
    refreshed: list[VideoGenerationJob] = []
    for j in jobs:
        refreshed.append(await _maybe_sync_status(db, j))
    return [_to_response(j) for j in refreshed]


async def _maybe_sync_status(
    db: AsyncSession, job: VideoGenerationJob
) -> VideoGenerationJob:
    """Refresh status from the provider if non-terminal and config still exists.

    Catches provider errors and stores them on the row without raising — a
    poll endpoint should never 500 because the upstream is flaky.
    """
    if job.status in ("completed", "failed"):
        return job
    if not job.provider_task_id:
        return job
    if not job.provider_config_id:
        # Config was deleted; we can't refresh anymore
        return job

    mp_repo = MediaProviderRepository(db)
    provider_config = await mp_repo.get_by_id(job.provider_config_id)
    if not provider_config:
        return job

    video_provider = get_video_provider(
        provider_config.provider, provider_config.credentials or {}
    )
    try:
        status = await video_provider.get_video_status(job.provider_task_id)
    except Exception as e:
        logger.warning(
            "get_video_status failed for job %s (task %s): %s",
            job.id, job.provider_task_id, e,
        )
        # Don't mark the job failed just because of a transient poll error.
        # Leave status unchanged so the next poll will retry.
        return job

    # ── B-roll orchestration ──
    broll_tasks = (job.params or {}).get("broll_tasks")
    if broll_tasks and status.status == "completed" and status.video_url:
        # Avatar video is done. Check B-roll tasks.
        if (job.params or {}).get("broll_composited"):
            # Already composited — just update with final result
            pass
        elif (job.params or {}).get("broll_compositing"):
            # Compositing in progress — leave as processing
            job.status = "processing"
            job.progress = 95
            await db.commit()
            await db.refresh(job)
            return job
        else:
            # Resolve the B-roll provider (may differ from avatar provider).
            broll_provider_config_id = (job.params or {}).get("broll_provider_config_id")
            broll_video_provider = video_provider  # fall back to avatar provider
            broll_cfg = None  # noqa: F841 (referenced in the warnings block below)
            if broll_provider_config_id:
                try:
                    broll_cfg = await mp_repo.get_by_id(uuid.UUID(broll_provider_config_id))
                    if broll_cfg:
                        broll_video_provider = get_video_provider(
                            broll_cfg.provider, broll_cfg.credentials or {}
                        )
                except Exception as e:
                    logger.warning("B-roll poll: failed to resolve broll provider: %s", e)

            # Check all B-roll tasks
            all_done = True
            any_failed = False
            broll_clips: list[dict] = []
            poll_failures: list[dict] = []  # captured for params.broll_warnings
            # Storage adapter for resolving direct-asset paths at poll
            # time. Only LocalStorageAdapter is wired today; if/when an
            # S3 adapter ships, ``get_absolute_path`` may need to return
            # a presigned URL and compositor's _download will route it
            # through the http branch.
            from app.adapters.storage import LocalStorageAdapter
            poll_storage = LocalStorageAdapter()

            for bt in broll_tasks:
                # Direct-use asset entries skip the provider poll entirely.
                # ``source == "asset"`` is the sentinel; we resolve the
                # asset's storage path on the fly here (rather than caching
                # it in params) so we (a) don't leak server FS layout via
                # the jobs API, (b) catch "asset deleted between submit
                # and composite" gracefully without crashing the job.
                if bt.get("source") == "asset" and bt.get("asset_id"):
                    try:
                        from app.application.broll_matching_service import (
                            get_asset_url_for_broll,
                        )
                        asset_uri, _ = await get_asset_url_for_broll(
                            db, uuid.UUID(bt["asset_id"]),
                        )
                        local_path = poll_storage.get_absolute_path(asset_uri)
                    except Exception as e:
                        logger.warning(
                            "B-roll asset %s missing at composite (skipping): %s",
                            bt.get("asset_id"), e,
                        )
                        continue  # skip this clip; rest of broll proceeds
                    broll_clips.append({
                        "url": local_path,
                        "insert_after_char": bt.get("insert_after_char", 0),
                        "duration_seconds": bt.get("duration_seconds", 5),
                        "type": bt.get("type", "illustrative"),
                        "prompt": bt.get("prompt", ""),
                        # Direct-mode flag — compositor maps audio from
                        # the broll clip (instead of from avatar) when set.
                        # AI-generated clips never carry meaningful audio,
                        # so this is implicitly false for non-asset entries.
                        "asset_audio": bool(bt.get("asset_audio")),
                    })
                    continue
                try:
                    br_status = await broll_video_provider.poll_broll_clip(bt["task_id"])
                except Exception as e:
                    logger.warning("B-roll poll failed for %s: %s", bt["task_id"], e)
                    all_done = False
                    continue
                if br_status["status"] == "completed" and br_status["output_urls"]:
                    broll_clips.append({
                        "url": br_status["output_urls"][0],
                        "insert_after_char": bt.get("insert_after_char", 0),
                        "duration_seconds": bt.get("duration_seconds", 5),
                        "type": bt.get("type", "illustrative"),
                        "prompt": bt.get("prompt", ""),
                    })
                elif br_status["status"] == "failed":
                    logger.warning("B-roll failed for %s: %s", bt.get("section_id") or bt.get("index"), br_status["error"])
                    any_failed = True
                    poll_failures.append({
                        "idx": bt.get("index", 0),
                        "prompt": (bt.get("prompt") or "")[:80],
                        "error": _classify_broll_error(str(br_status.get("error") or "unknown error")),
                    })
                    # Continue without this clip — not fatal
                else:
                    all_done = False

            # Surface polling-time failures the same way as submit-time ones.
            # They live in the same JSONB list so the UI doesn't need to know
            # about the distinction; ``broll_status`` rolls up partial vs all.
            # Dedupe by ``idx`` because the poll loop re-runs every status
            # check — without this each failed shot would accumulate copies.
            if poll_failures:
                p = dict(job.params or {})
                existing = list(p.get("broll_warnings") or [])
                seen_idx = {w.get("idx") for w in existing}
                for f in poll_failures:
                    if f.get("idx") not in seen_idx:
                        existing.append(f)
                        seen_idx.add(f.get("idx"))
                p["broll_warnings"] = existing
                # Resolve broll provider name for the UI breadcrumb.
                # The local variable is ``broll_cfg`` (loaded at the top
                # of this function), not ``broll_provider_config`` (which
                # is the parameter name in ``create_video_job`` — this
                # function is the poll path and uses different names).
                # Mixing the two produced ``NameError: name
                # 'broll_provider_config' is not defined`` on every poll
                # of a job whose B-roll had any failure.
                broll_provider_name = (
                    broll_cfg.provider if broll_cfg
                    else (provider_config.provider if provider_config else "")
                )
                p.setdefault("broll_warning_provider", broll_provider_name)
                if all_done and not broll_clips:
                    p["broll_status"] = "all_failed"
                    if not job.error_message:
                        first = poll_failures[0].get("error", "?")
                        job.error_message = (
                            f"B-roll generation failed for all {len(poll_failures)} shot(s): {first}"
                        )
                else:
                    p["broll_status"] = "partial"
                job.params = p

            if not all_done:
                # Avatar done, B-roll still processing
                job.status = "processing"
                job.progress = 80
                await db.commit()
                await db.refresh(job)
                return job

            if broll_clips:
                # All done — run compositing inline (download + FFmpeg).
                # This blocks the poll response for ~30-60s but guarantees execution.
                params = dict(job.params)
                params["broll_compositing"] = True
                job.params = params
                job.status = "processing"
                job.progress = 90
                await db.commit()

                from app.adapters.video.broll_compositor import composite_broll
                from app.config import settings
                output_dir = str(settings.STORAGE_BASE_PATH) + "/composited"
                try:
                    _p = job.params or {}
                    output_path = await composite_broll(
                        avatar_video_url=status.video_url,
                        broll_clips=broll_clips,
                        section_order=_p.get("broll_section_order", []),
                        sections=_p.get("broll_sections", {}),
                        output_dir=output_dir,
                        caption=_p.get("caption", True),
                        aspect_ratio=_p.get("aspect_ratio", "portrait"),
                        subtitle_style=_p.get("subtitle_style", "classic"),
                        subtitle_color=_p.get("subtitle_color"),
                        subtitle_stroke=_p.get("subtitle_stroke"),
                    )
                    video_url = f"/uploads/composited/{output_path.split('/')[-1]}"
                    logger.info("B-roll composite done: %s", video_url)
                    params["avatar_video_url"] = status.video_url
                    job.video_url = video_url
                    # Override chanjing's avatar-portrait cover_url with
                    # an actual frame from the composited mp4. This is
                    # the only path where the cover meaningfully differs
                    # — non-broll videos use chanjing's preview directly
                    # since their first frame ≈ avatar shot anyway.
                    real_cover_url = await _extract_video_cover(output_path)
                    if real_cover_url:
                        job.cover_url = real_cover_url
                except Exception as e:
                    logger.exception("B-roll compositing failed: %s", e)
                    job.video_url = status.video_url  # fallback to avatar-only
                    params["broll_error"] = str(e)[:200]

                params["broll_compositing"] = False
                params["broll_composited"] = True
                job.params = params
                job.status = "completed"
                # If we wrote a real cover above, don't let chanjing's
                # avatar-portrait clobber it. Only fall back when the
                # extraction failed (real_cover_url None).
                if status.cover_url and not job.cover_url:
                    job.cover_url = status.cover_url
                if status.duration_seconds is not None:
                    job.duration_seconds = status.duration_seconds
                job.finished_at = _utcnow()
                await db.commit()
                await db.refresh(job)
                return job
            # else: no B-roll clips succeeded, fall through to normal completion

    job.status = status.status
    job.progress = status.progress
    if status.video_url:
        job.video_url = status.video_url
    if status.cover_url:
        job.cover_url = status.cover_url
    if status.duration_seconds is not None:
        job.duration_seconds = status.duration_seconds
    if status.error_message:
        job.error_message = status.error_message[:1000]
    if status.status in ("completed", "failed") and not job.finished_at:
        job.finished_at = _utcnow()

    await db.commit()
    await db.refresh(job)
    return job


# ── Delete ──────────────────────────────────────────────────────────


async def delete_video_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Delete the local job row. Does NOT delete the remote video on the provider."""
    job_repo = VideoJobRepository(db)
    job = await job_repo.get_by_id(job_id)
    if not job:
        raise NotFoundError("VideoGenerationJob", str(job_id))
    await job_repo.delete(job)
    await db.commit()


# ── Global Video Studio listing ─────────────────────────────────────


async def list_all_videos(
    db: AsyncSession,
    *,
    status: str | None = None,
    provider: str | None = None,
    offer_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    refresh_inflight: bool = True,
) -> tuple[list[VideoJobWithCreationResponse], int]:
    """Cross-creation video listing for the Video Studio page.

    Joins each job with its parent Creation so the UI can display the source
    creation's title without an extra fetch. Optionally lazy-refreshes any
    non-terminal jobs on this page (capped to keep the response fast).
    """
    offset = (page - 1) * page_size

    # Build the base query joined with Creation
    base = (
        select(VideoGenerationJob, Creation)
        .join(Creation, VideoGenerationJob.creation_id == Creation.id)
    )
    count_base = (
        select(func.count())
        .select_from(VideoGenerationJob)
        .join(Creation, VideoGenerationJob.creation_id == Creation.id)
    )

    if status:
        base = base.where(VideoGenerationJob.status == status)
        count_base = count_base.where(VideoGenerationJob.status == status)
    if provider:
        base = base.where(VideoGenerationJob.provider == provider)
        count_base = count_base.where(VideoGenerationJob.provider == provider)
    if offer_id:
        base = base.where(Creation.offer_id == offer_id)
        count_base = count_base.where(Creation.offer_id == offer_id)

    total = (await db.execute(count_base)).scalar_one()
    rows = await db.execute(
        base.order_by(VideoGenerationJob.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    pairs = list(rows.all())

    # Lazy-refresh any in-flight jobs in this page (small N, so safe).
    # Skip if caller asked us not to (e.g. unit tests, paginating fast).
    if refresh_inflight:
        for job, _creation in pairs:
            if job.status in ("pending", "processing"):
                await _maybe_sync_status(db, job)

    items = [_to_response_with_creation(job, creation) for job, creation in pairs]
    return items, total


def _to_response_with_creation(
    job: VideoGenerationJob, creation: Creation
) -> VideoJobWithCreationResponse:
    base = _to_response(job)
    return VideoJobWithCreationResponse(
        **base.model_dump(),
        creation_title=creation.title,
        creation_content_type=creation.content_type,
    )
