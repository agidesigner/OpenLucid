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

    # Brandkit-driven subtitle-color defaults. When the user hasn't picked
    # an explicit override, the brandkit's primary / secondary colors stand
    # in for preset defaults — this way video subtitles visually match the
    # brand without forcing the user to re-enter hex on every generation.
    # Explicit user choice (non-null ``data.subtitle_color`` / ``stroke``)
    # always wins; only the unset case gets filled.
    effective_subtitle_color = data.subtitle_color
    effective_subtitle_stroke = data.subtitle_stroke
    if effective_subtitle_color is None or effective_subtitle_stroke is None:
        from app.models.brandkit import BrandKit, BrandKitColor
        try:
            kit = (await db.execute(
                select(BrandKit).where(
                    BrandKit.scope_type == "offer",
                    BrandKit.scope_id == creation.offer_id,
                ).order_by(BrandKit.updated_at.desc()).limit(1)
            )).scalars().first()
            if kit is None and creation.merchant_id:
                kit = (await db.execute(
                    select(BrandKit).where(
                        BrandKit.scope_type == "merchant",
                        BrandKit.scope_id == creation.merchant_id,
                    ).order_by(BrandKit.updated_at.desc()).limit(1)
                )).scalars().first()
            if kit is not None:
                colors = (await db.execute(
                    select(BrandKitColor)
                    .where(BrandKitColor.brandkit_id == kit.id)
                    .order_by(BrandKitColor.priority)
                )).scalars().all()
                by_role = {c.role: c.hex for c in colors}
                if effective_subtitle_color is None and by_role.get("primary"):
                    effective_subtitle_color = by_role["primary"]
                if effective_subtitle_stroke is None and by_role.get("secondary"):
                    effective_subtitle_stroke = by_role["secondary"]
                if effective_subtitle_color or effective_subtitle_stroke:
                    logger.info(
                        "Subtitle color defaults pulled from brandkit: fill=%s stroke=%s",
                        effective_subtitle_color, effective_subtitle_stroke,
                    )
        except Exception as e:
            logger.warning("Brandkit color lookup failed (using style-preset defaults): %s", e)

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
    try:
        provider_task_id = await video_provider.create_avatar_video(create_req)
    except Exception as e:
        # Persist failure on the job row before re-raising
        logger.warning("create_avatar_video failed for job %s: %s", job.id, e)
        job.status = "failed"
        job.error_message = str(e)[:1000]
        job.finished_at = _utcnow()
        await db.commit()
        await db.refresh(job)
        raise

    # 6. Success — record provider_task_id and move to processing
    job.provider_task_id = provider_task_id
    job.status = "processing"
    job.started_at = _utcnow()

    # 7. If B-roll requested, use the AI-director's broll_plan from structured_content.
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
            broll_provider_config = None
            broll_model_code = None
            try:
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
                            "B-roll: using provider=%s model=%s (avatar=%s)",
                            broll_provider_config.provider, broll_model_code, provider_config.provider,
                        )
                    else:
                        logger.info("B-roll: video_gen default provider_config_id points at a missing row")
            except Exception as e:
                logger.warning("B-roll: failed to resolve video_gen default: %s", e)

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
                # Reference images for B-roll generation come from the
                # offer's Assets tab — the only place the UI lets users
                # upload product-specific photos. Merchant-scope Assets
                # aren't queried because no UI currently uploads to that
                # scope; that query would always be empty in practice.
                # Brandkit is intentionally NOT used as a source — it's
                # brand identity (logo, colors, fonts), not product
                # visual content for i2v conditioning.
                from app.adapters.storage import LocalStorageAdapter
                from app.models.asset import Asset
                storage = LocalStorageAdapter()

                candidates: list = []
                source_label = "none"
                try:
                    result = await db.execute(
                        select(Asset).where(
                            Asset.scope_type == "offer",
                            Asset.scope_id == creation.offer_id,
                            Asset.asset_type == "image",
                            Asset.mime_type.in_(["image/png", "image/jpeg", "image/webp"]),
                        ).order_by(Asset.created_at.desc()).limit(5)
                    )
                    candidates = list(result.scalars().all())
                    if candidates:
                        source_label = "offer_kb"
                except Exception as e:
                    logger.warning("B-roll: offer-KB asset lookup failed: %s", e)

                # Upload each candidate; per-asset try so one bad file
                # doesn't kill the rest. Empty ``candidates`` is fine —
                # Seedance just runs pure text-to-video without ref_img.
                asset_urls: list[str] = []
                for asset in candidates:
                    try:
                        file_bytes = await storage.get_file(asset.storage_uri)
                        ref_url = await broll_video_provider.upload_temp_file(
                            file_bytes, asset.file_name or "ref.png",
                        )
                        asset_urls.append(ref_url)
                    except Exception as e:
                        logger.warning("B-roll: failed to upload asset %s: %s", asset.file_name, e)
                logger.info(
                    "B-roll: %d reference images uploaded (source=%s)",
                    len(asset_urls), source_label,
                )

                # Cap aligned with composer spec (up to 5 inserts for 90s+).
                # Build specs synchronously (cheap prep), then fan out the
                # provider calls with a bounded semaphore. Previously this
                # loop awaited each ``submit_broll_clip`` serially; for 3-5
                # broll clips at ~5-30s each, total time dominated the job
                # submission latency. Parallelism cuts that roughly in half
                # (ceil(N / BROLL_SUBMIT_CONCURRENCY) waves instead of N).
                broll_specs: list[tuple[int, dict, str, int, dict]] = []
                for idx, entry in enumerate(broll_plan[:5]):
                    prompt = (entry.get("prompt") or "").strip()
                    if not prompt:
                        continue
                    shot_type = entry.get("type", "illustrative")
                    # Retention opener: prepend style cues that nudge Seedance
                    # toward a stopping-power shot. Without this, retention
                    # and illustrative shots look identical — which defeats
                    # the whole point of the type distinction.
                    if shot_type == "retention":
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
                    ref_img = asset_urls[idx % len(asset_urls)] if asset_urls else None
                    submit_kwargs: dict = dict(
                        prompt=prompt, duration=dur,
                        aspect_ratio=ar, ref_img_url=ref_img,
                    )
                    if broll_model_code:
                        submit_kwargs["model_code"] = broll_model_code
                    broll_specs.append((idx, entry, prompt, dur, submit_kwargs))

                # Concurrency cap: 3 keeps us well under Jogg/Chanjing per-key
                # rate limits (observed ~5 requests/sec ceilings in testing)
                # while still cutting total submit time by ~50% for a typical
                # 3-5 broll batch. Tune down if a provider starts 429-ing.
                BROLL_SUBMIT_CONCURRENCY = 3
                _sem = asyncio.Semaphore(BROLL_SUBMIT_CONCURRENCY)

                async def _submit_one(
                    idx: int, entry: dict, prompt: str, dur: int, submit_kwargs: dict
                ) -> dict | None:
                    async with _sem:
                        try:
                            task_id = await broll_video_provider.submit_broll_clip(**submit_kwargs)
                        except Exception as e:
                            # Per-item isolation: one failure must not sink
                            # the batch. Matches the previous serial loop's
                            # warn-and-continue behavior.
                            logger.warning("B-roll #%d submit failed: %s", idx, e)
                            return None
                        logger.info(
                            "B-roll #%d submitted via %s: type=%s char=%s task=%s",
                            idx, broll_provider_config.provider, entry.get("type"),
                            entry.get("insert_after_char"), task_id,
                        )
                        return {
                            "index": idx,
                            "task_id": task_id,
                            "type": entry.get("type", "illustrative"),
                            "insert_after_char": entry.get("insert_after_char", 0),
                            "duration_seconds": dur,
                            "prompt": prompt,
                        }

                results = await asyncio.gather(
                    *(_submit_one(*s) for s in broll_specs)
                )
                # ``gather`` preserves argument order, so the surviving tasks
                # stay in narration order without an explicit sort.
                broll_tasks: list[dict] = [r for r in results if r is not None]

                if broll_tasks:
                    params["broll_tasks"] = broll_tasks
                    params["broll_section_order"] = section_order
                    params["broll_sections"] = {sid: dict(sections.get(sid, {})) for sid in section_order}
                    params["broll_mode"] = "cutaway"
                    # Remember which provider to poll for the B-roll tasks — may differ from avatar provider
                    params["broll_provider_config_id"] = str(broll_provider_config.id)
                    job.params = params

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
            for bt in broll_tasks:
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
                    # Continue without this clip — not fatal
                else:
                    all_done = False

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
                except Exception as e:
                    logger.exception("B-roll compositing failed: %s", e)
                    job.video_url = status.video_url  # fallback to avatar-only
                    params["broll_error"] = str(e)[:200]

                params["broll_compositing"] = False
                params["broll_composited"] = True
                job.params = params
                job.status = "completed"
                if status.cover_url:
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


