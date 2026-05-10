"""Image-generation service.

Two flows share the ``image_generation_jobs`` table and a single provider
adapter (GPT-image-1):

  * ``create_poster_job``        — full template flow with style anchor +
                                   PIL composite. Output: standalone image
                                   URL on the job row.
  * ``create_article_cover_job`` — light flow with prompt only. Output:
                                   image URL written back to ``creations``.

Both run **synchronously** inside the request — GPT-image-1 returns
image bytes in the HTTP response, so there's no polling. Total request
time is bounded by the OpenAI call (5–15s typical). For long-running
backends we'd switch to a task queue, but that's out of scope until at
least one async provider lands.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.image.base import (
    GenerateImageRequest,
    GenerateImageResult,
    GenerateWithReferencesRequest,
    ImageProvider,
    UnsupportedReferenceMode,
)
from app.adapters.image.factory import get_image_provider
from app.adapters.image.gpt_image import GPTImageProvider
from app.adapters.storage import LocalStorageAdapter
from app.config import settings
from app.context import get_effective_prompt
from app.application import style_extractor
from app.application.image_template import (
    Template,
    get_template,
    render_poster,
)
from app.exceptions import AppError, NotFoundError
from app.models.brandkit import BrandKit
from app.models.brandkit_asset_link import BrandKitAssetLink
from app.models.asset import Asset
from app.models.creation import Creation
from app.models.image_generation_job import ImageGenerationJob
from app.models.llm_config import LLMConfig
from app.models.media_provider_config import MediaProviderConfig
from app.models.offer import Offer
from app.schemas.image_generation import (
    ArticleCoverJobCreate,
    BriefJobCreate,
    CoverSuggestionResponse,
    ImageJobResponse,
    PosterJobCreate,
    ReferenceUploadResponse,
    ReferenceUploadInput,
    ReferenceSuggestion,
    RefineJobCreate,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _PromptFormatMap(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _render_prompt_template(
    template: str,
    *,
    preset_key: str,
    fallback_getter: Any,
    context: dict[str, Any],
) -> str:
    def _apply(raw: str) -> str:
        rendered = raw
        for key, value in sorted(context.items(), key=lambda item: len(item[0]), reverse=True):
            rendered = rendered.replace(f"{{{key}}}", "" if value is None else str(value))
        return rendered

    try:
        return _apply(template)
    except Exception:
        logger.warning(
            "Prompt preset %s failed to render, using default template",
            preset_key,
            exc_info=True,
        )
        return _apply(fallback_getter())


def _default_image_brief_template() -> str:
    from app.application.prompt_preset_service import _get_image_brief_template

    return _get_image_brief_template()


def _default_image_refine_template() -> str:
    from app.application.prompt_preset_service import _get_image_refine_template

    return _get_image_refine_template()


def _default_cover_derive_prompt() -> str:
    from app.application.prompt_preset_service import _get_cover_derive_prompt

    return _get_cover_derive_prompt()


_REFERENCE_UPLOAD_ROLES = {"supplemental", "qr"}


def _to_response(job: ImageGenerationJob) -> ImageJobResponse:
    return ImageJobResponse(
        id=str(job.id),
        mode=job.mode,  # type: ignore[arg-type]
        creation_id=str(job.creation_id) if job.creation_id else None,
        offer_id=str(job.offer_id) if job.offer_id else None,
        brandkit_id=str(job.brandkit_id) if job.brandkit_id else None,
        template_id=job.template_id,
        provider=job.provider,
        provider_config_id=str(job.provider_config_id) if job.provider_config_id else None,
        status=job.status,  # type: ignore[arg-type]
        params=job.params or {},
        image_url=job.image_url,
        preview_url=job.preview_url,
        progress=job.progress,
        error_message=job.error_message,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        created_at=job.created_at.isoformat() if job.created_at else "",
        updated_at=job.updated_at.isoformat() if job.updated_at else "",
    )


# ── Provider resolution ─────────────────────────────────────────────


async def _resolve_image_provider(
    db: AsyncSession,
    *,
    provider_config_id: str | uuid.UUID | None = None,
    model_code: str | None = None,
) -> tuple[ImageProvider, uuid.UUID | None, str, str]:
    """Pick an image provider + model, honoring caller > default > legacy.

    Returns ``(provider_instance, provider_config_id_or_None, source_label, model_code)``.

    Resolution order:
      1. Caller-supplied ``provider_config_id`` + ``model_code`` (per-job
         override surfaced from BriefJobCreate / RefineJobCreate).
      2. ``MediaCapabilityDefault('image_gen')`` — the system default the
         user picked at /settings/media-capabilities.
      3. Legacy: dedicated ``media_provider_configs`` row with
         provider='openai_image' AND is_active.
      4. Legacy: any active ``llm_configs`` row with provider='openai'.

    Raises ``UNSUPPORTED_IMAGE_PROVIDER`` when the resolved provider is
    not OpenAI — chanjing / google adapters land in Phase 2. The error
    message tells the user how to point the system default back at OpenAI.
    """
    # ── 1. Caller-supplied per-job override ──
    target_pcid: uuid.UUID | None = None
    target_model = model_code or None
    if provider_config_id:
        try:
            target_pcid = (
                provider_config_id
                if isinstance(provider_config_id, uuid.UUID)
                else uuid.UUID(str(provider_config_id))
            )
        except (ValueError, TypeError):
            raise AppError(
                "INVALID_PROVIDER_CONFIG_ID",
                f"image_provider_config_id is not a valid UUID: {provider_config_id!r}",
                400,
            )

    # ── 2. System default ──
    if target_pcid is None:
        from app.models.media_capability_default import MediaCapabilityDefault

        default_row = (
            await db.execute(
                select(MediaCapabilityDefault).where(
                    MediaCapabilityDefault.capability == "image_gen"
                )
            )
        ).scalars().first()
        if default_row:
            target_pcid = default_row.provider_config_id
            target_model = target_model or default_row.model_code

    # ── 3. Resolve target_pcid → credentials + provider name ──
    if target_pcid is not None:
        # First try real media_provider_configs row.
        mpc = await db.get(MediaProviderConfig, target_pcid)
        if mpc and mpc.is_active:
            return _build_image_provider_from_mpc(
                mpc, model_code=target_model, source="media_provider_config"
            )
        # Fallback: the id might point at an LLMConfig (when the
        # capabilities-options builder synthesized a virtual openai
        # provider — see get_media_capability_configs).
        llm_via_id = await db.get(LLMConfig, target_pcid)
        if llm_via_id and llm_via_id.provider == "openai" and llm_via_id.is_active:
            return _build_image_provider_from_llm(
                llm_via_id, model_code=target_model
            )

    # ── 4. Legacy: dedicated openai_image media-provider row ──
    legacy_mpc = (
        await db.execute(
            select(MediaProviderConfig).where(
                MediaProviderConfig.provider == "openai_image",
                MediaProviderConfig.is_active.is_(True),
            )
        )
    ).scalars().first()
    if legacy_mpc:
        return _build_image_provider_from_mpc(
            legacy_mpc, model_code=target_model, source="media_provider_config"
        )

    # ── 5. Legacy: active OpenAI LLMConfig ──
    llm_row = (
        await db.execute(
            select(LLMConfig).where(
                LLMConfig.provider == "openai",
                LLMConfig.is_active.is_(True),
            )
        )
    ).scalars().first()
    if llm_row and llm_row.api_key:
        return _build_image_provider_from_llm(llm_row, model_code=target_model)

    raise AppError(
        "IMAGE_PROVIDER_NOT_CONFIGURED",
        "No OpenAI-compatible image provider is configured. "
        "Add an OpenAI LLM in Settings → AI Models, or pick an image-gen "
        "provider in Settings → Media Capabilities.",
        400,
    )


def _build_image_provider_from_mpc(
    mpc: MediaProviderConfig,
    *,
    model_code: str | None,
    source: str,
) -> tuple[ImageProvider, uuid.UUID | None, str, str]:
    """Build a provider instance from a media_provider_configs row.

    Dispatches via the image-adapter factory so chanjing / google / openai
    all work uniformly. The factory raises UNKNOWN_IMAGE_PROVIDER for
    truly unrecognized provider names.
    """
    creds = mpc.credentials or {}
    chosen_model = (
        model_code
        or creds.get("model")
        or _default_model_for_provider(mpc.provider)
    )
    return (
        get_image_provider(mpc.provider, creds, model=chosen_model),
        mpc.id,
        source,
        chosen_model,
    )


def _build_image_provider_from_llm(
    llm: LLMConfig,
    *,
    model_code: str | None,
) -> tuple[ImageProvider, uuid.UUID | None, str, str]:
    """Build a provider instance from an OpenAI LLMConfig (legacy path)."""
    chosen_model = model_code or settings.IMAGE_MODEL
    return (
        get_image_provider(
            "openai",
            credentials={
                "api_key": llm.api_key,
                "base_url": (llm.base_url or "").strip() or None,
            },
            model=chosen_model,
        ),
        None,  # not a real MPC row
        "llm_config",
        chosen_model,
    )


def _default_model_for_provider(provider: str) -> str:
    """Pick the recommended default when no explicit model_code is set.

    Mirrors the "first model under each provider" rule the frontend uses
    when no MediaCapabilityDefault row has been written yet — the registry
    in setting_service intentionally lists the recommended model first.
    """
    return {
        "chanjing":     "doubao-seedream-4.5",
        "google":       "gemini-3-pro-image-preview",
        "openai":       "gpt-image-2",
        "openai_image": "gpt-image-2",
    }.get(provider, settings.IMAGE_MODEL)


# ── Common helpers ──────────────────────────────────────────────────


async def _get_offer_brandkit(
    db: AsyncSession, offer_id: uuid.UUID
) -> BrandKit | None:
    return (
        await db.execute(
            select(BrandKit).where(
                BrandKit.scope_type == "offer",
                BrandKit.scope_id == offer_id,
            )
        )
    ).scalars().first()


async def _load_logo_bytes(
    db: AsyncSession, brandkit: BrandKit | None, storage: LocalStorageAdapter
) -> bytes | None:
    if not brandkit:
        return None
    link = (
        await db.execute(
            select(BrandKitAssetLink).where(
                BrandKitAssetLink.brandkit_id == brandkit.id,
                BrandKitAssetLink.role == "logo",
            )
        )
    ).scalars().first()
    if not link:
        return None
    asset = await db.get(Asset, link.asset_id)
    if not asset or not asset.storage_uri:
        return None
    try:
        return await storage.get_file(asset.storage_uri)
    except Exception as e:
        logger.warning("Logo file load failed for asset %s: %s", asset.id, e)
        return None


async def _load_qr_bytes(
    data: PosterJobCreate,
    storage: LocalStorageAdapter,
    *,
    offer_id: uuid.UUID | None = None,
) -> bytes | None:
    """Legacy poster QR loader — only the storage-URI path is supported.

    Hardened with the same path-traversal guard the brief flow uses:
    ``os.path.normpath`` is applied and the first path segment must be
    the offer's id. Without the offer_id we fall back to the legacy
    permissive behavior (back-compat for jobs without offer context),
    but every modern caller passes it.
    """
    if not data.qr_asset_uri:
        return None
    if offer_id is not None:
        normalized = _safe_offer_subpath(data.qr_asset_uri, offer_id)
        if not normalized:
            logger.warning(
                "Legacy QR uri rejected (normpath guard): uri=%s offer=%s",
                data.qr_asset_uri,
                offer_id,
            )
            return None
        target = normalized
    else:
        target = data.qr_asset_uri
    try:
        return await storage.get_file(target)
    except Exception as e:
        logger.warning("QR asset load failed (%s): %s", target, e)
        return None


_REFERENCE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_REFERENCE_UPLOAD_ALLOWED_FORMATS = ("png", "jpeg", "webp", "gif")


async def save_reference_upload(
    *,
    file_content: bytes,
    file_name: str,
    offer_id: uuid.UUID,
    role: str = "supplemental",
) -> ReferenceUploadResponse:
    """Persist a one-off Image Studio reference upload.

    This deliberately bypasses the Asset table: no asset row, no tagging
    job, no future library recommendation. The returned upload_id is a
    storage URI under ``tmp/image_refs/<offer_id>/`` and is validated
    again when a generation request consumes it.

    Validation gates (all server-side; the upload endpoint trusts none
    of them to a happy-path frontend):
      * non-empty bytes
      * size <= ``_REFERENCE_UPLOAD_MAX_BYTES``
      * magic bytes match an allow-listed image format
      * PIL ``verify()`` parses the bytes (catches truncated / corrupt
        files that would otherwise fail mid-generation)
    """
    clean_role = role if role in _REFERENCE_UPLOAD_ROLES else "supplemental"
    if not file_content:
        raise AppError("EMPTY_REFERENCE_UPLOAD", "Uploaded reference image is empty", 400)
    if len(file_content) > _REFERENCE_UPLOAD_MAX_BYTES:
        raise AppError(
            "REFERENCE_UPLOAD_TOO_LARGE",
            f"Reference upload exceeds the {_REFERENCE_UPLOAD_MAX_BYTES // (1024 * 1024)}MB limit",
            413,
        )
    fmt = _detect_image_format(file_content)
    if fmt not in _REFERENCE_UPLOAD_ALLOWED_FORMATS:
        raise AppError(
            "INVALID_REFERENCE_FORMAT",
            "Reference upload must be PNG / JPEG / WebP / GIF",
            400,
        )
    # PIL verify reads structural headers; a corrupt JPEG slips past
    # magic-byte sniffing but fails here. Verify on a fresh BytesIO so
    # we don't disturb the caller's bytes.
    try:
        from PIL import Image as _PILImage

        with _PILImage.open(io.BytesIO(file_content)) as img:
            img.verify()
    except Exception as e:
        raise AppError(
            "CORRUPT_REFERENCE_IMAGE",
            f"Reference upload is not a parseable image: {e}",
            400,
        ) from e

    safe_name = _safe_upload_filename(file_name)
    storage = LocalStorageAdapter()
    storage_uri = await storage.save_file(
        file_content,
        safe_name,
        sub_path=f"tmp/image_refs/{offer_id}",
    )
    return ReferenceUploadResponse(
        upload_id=storage_uri,
        url=f"/uploads/{storage_uri}",
        role=clean_role,  # type: ignore[arg-type]
        label=safe_name,
    )


def _detect_image_format(raw: bytes) -> str | None:
    """Return one of {png, jpeg, gif, webp} or None for unrecognized bytes.

    Magic-byte sniff only — extension/MIME from the multipart upload is
    NOT trusted because a misnamed file or a hostile multipart can lie."""
    if not raw:
        return None
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "webp"
    return None


def _safe_upload_filename(file_name: str) -> str:
    """Sanitize an upload filename without losing readable content.

    Strategy: blacklist only the characters that are actually dangerous on
    a filesystem (path separators, NUL, control chars, DEL) and let the
    rest through — including CJK, accented letters, spaces, and other
    Unicode that users legitimately put in filenames. The previous
    implementation used a strict ASCII whitelist which collapsed
    ``测试截图.png`` to ``_.png`` and lost the user's labeling intent.

    Path-traversal is still neutralized: we always take the basename
    after splitting on both ``/`` and ``\\`` before sanitizing, so
    ``../../etc/passwd`` resolves to ``passwd`` regardless of the regex.
    """
    raw = (file_name or "reference").replace("\\", "/").split("/")[-1].strip()
    if not raw:
        raw = "reference"
    # Strip path separators (defense-in-depth — already split above) +
    # NUL + ASCII control chars + DEL. Whitespace runs collapse to "_".
    cleaned = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", raw)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._- ")
    if not cleaned:
        cleaned = "reference"
    return cleaned[:120]


# ── Poster flow ─────────────────────────────────────────────────────


async def create_poster_job(
    db: AsyncSession,
    data: PosterJobCreate,
) -> ImageJobResponse:
    """Synchronous create + run for a poster generation."""
    template = get_template(data.template_id)
    if not template:
        raise AppError(
            "UNKNOWN_TEMPLATE",
            f"Unknown template_id: {data.template_id!r}",
            400,
        )

    try:
        offer_uuid = uuid.UUID(data.offer_id)
    except (ValueError, TypeError) as e:
        raise AppError("INVALID_OFFER_ID", "offer_id is not a valid UUID", 400) from e
    offer = await db.get(Offer, offer_uuid)
    if not offer:
        raise NotFoundError("Offer", data.offer_id)

    brandkit = await _get_offer_brandkit(db, offer_uuid)
    provider, provider_cfg_id, source_label, resolved_model_code = await _resolve_image_provider(db)

    job = ImageGenerationJob(
        mode="poster",
        offer_id=offer_uuid,
        brandkit_id=brandkit.id if brandkit else None,
        template_id=template.id,
        provider=provider.provider_name,
        provider_config_id=provider_cfg_id,
        status="pending",
        params={
            "selling_point": data.selling_point,
            "slot_values": data.slot_values,
            "aspect_ratio": data.aspect_ratio,
            "qr_asset_uri": data.qr_asset_uri,
            "provider_source": source_label,
        },
    )
    db.add(job)
    await db.flush()
    await db.commit()
    await db.refresh(job)

    # ── Run synchronously ──
    job.status = "processing"
    job.started_at = _utcnow()
    await db.commit()

    storage = LocalStorageAdapter()

    try:
        logo_bytes = await _load_logo_bytes(db, brandkit, storage)
        qr_bytes = await _load_qr_bytes(data, storage, offer_id=offer_uuid)

        # Primary path: trust the model. Hand it the brand's reference
        # posters + logo (+ optional QR) and let it compose end-to-end.
        # No PIL renderer, no slot positioning, no contrast-aware tricks.
        ref_posters = await style_extractor.pick_reference_posters(
            db, offer_id=offer_uuid, selling_point=data.selling_point, limit=2
        )
        ref_bytes = await _load_reference_poster_bytes(storage, ref_posters)

        ai_composed_path_used = False
        final_image_bytes: bytes | None = None
        prompt_used = ""

        if ref_bytes or logo_bytes:
            edits_prompt = _build_edits_prompt(
                template,
                data,
                has_logo=bool(logo_bytes),
                has_qr=bool(qr_bytes),
            )
            references = list(ref_bytes)
            if logo_bytes:
                references.append(logo_bytes)
            if qr_bytes:
                references.append(qr_bytes)

            try:
                edits_result = await provider.generate_with_references(
                    GenerateWithReferencesRequest(
                        prompt=edits_prompt,
                        references=references,
                        aspect_ratio=template.aspect_ratio,  # type: ignore[arg-type]
                        quality="standard",
                    )
                )
                final_image_bytes = edits_result.image_bytes
                prompt_used = edits_prompt
                ai_composed_path_used = True
            except UnsupportedReferenceMode as e:
                # Provider can't take image inputs — fall back to PIL.
                logger.info("Edits path unsupported, falling back to PIL: %s", e)

        if final_image_bytes is None:
            # Fallback: text-only generation + PIL composition. Kept while
            # users sit behind proxies that don't expose /v1/images/edits;
            # will be retired once trust-the-model is universally usable.
            anchor = await style_extractor.get_style_anchor(
                db, offer_id=offer_uuid, selling_point=data.selling_point
            )
            await db.commit()  # persist cache write
            style_summary = style_extractor.render_style_summary(
                anchor, brand_voice=brandkit.brand_voice if brandkit else None
            )
            prompt_used = template.background_prompt_template.format(
                style_summary=style_summary
            )

            gen_result = await provider.generate_image(
                GenerateImageRequest(
                    prompt=prompt_used,
                    aspect_ratio=template.aspect_ratio,  # type: ignore[arg-type]
                    quality="standard",
                )
            )

            slot_inputs: dict[str, Any] = dict(data.slot_values or {})
            if qr_bytes:
                slot_inputs["qr"] = qr_bytes

            final_image_bytes = render_poster(
                template,
                background_bytes=gen_result.image_bytes,
                slot_values=slot_inputs,
                logo_bytes=logo_bytes,
            )

        sub_path = f"generated_images/{offer_uuid}"
        storage_uri = await storage.save_file(
            final_image_bytes,
            file_name=f"{job.id}.png",
            sub_path=sub_path,
        )
        public_url = f"/uploads/{storage_uri}"

        job.image_url = public_url
        job.preview_url = public_url
        job.status = "completed"
        job.finished_at = _utcnow()
        job.params = {
            **(job.params or {}),
            "prompt": prompt_used,
            "render_path": "ai_composed" if ai_composed_path_used else "pil_composed",
        }
        await db.commit()
    except AppError as e:
        job.status = "failed"
        job.error_message = f"{e.code}: {e.message}"
        job.finished_at = _utcnow()
        await db.commit()
        raise
    except Exception as e:
        logger.exception("Poster generation failed for job %s", job.id)
        job.status = "failed"
        job.error_message = f"INTERNAL: {e}"
        job.finished_at = _utcnow()
        await db.commit()
        raise AppError("IMAGE_GENERATION_FAILED", str(e), 500) from e

    await db.refresh(job)
    return _to_response(job)


def _build_edits_prompt(
    template: Template, data: PosterJobCreate, *, has_logo: bool, has_qr: bool
) -> str:
    """Build the prompt for the trust-the-model edits path.

    The model receives the references as image inputs, so the prompt is
    short and behavioral — what to compose, not what to look like. We
    intentionally avoid restating style cues already visible in the
    reference posters; the model SEES them.
    """
    slots = data.slot_values or {}
    title = (slots.get("title") or data.selling_point or "").strip()
    subtitle = (slots.get("subtitle") or "").strip()
    cta = (slots.get("cta") or "").strip()
    date = (slots.get("date") or "").strip()
    venue = (slots.get("venue") or "").strip()
    deadline = (slots.get("deadline") or "").strip()

    text_lines = []
    if title:
        text_lines.append(f"Headline (large, prominent): 「{title}」")
    if subtitle:
        text_lines.append(f"Subtitle (smaller, supporting): 「{subtitle}」")
    if date:
        text_lines.append(f"Date chip: 「{date}」")
    if venue:
        text_lines.append(f"Venue line: 「{venue}」")
    if deadline:
        text_lines.append(f"Deadline chip: 「{deadline}」")
    if cta:
        text_lines.append(f"CTA button text: 「{cta}」")

    parts = [
        f"Create a vertical {template.aspect_ratio} marketing poster for the brand.",
        "Match the visual style, color palette, lighting, and typography aesthetic of the provided reference poster(s).",
        "",
        "Brand assets provided as additional reference images (after the style reference posters):",
    ]
    if has_logo:
        parts.extend(
            [
                "  - The provided brand logo is the ONLY logo / brand mark allowed in the poster.",
                "  - Place that provided logo once, in the top-left corner. Recolor it as needed so it stays readable against the chosen background.",
                "  - Do NOT invent, redraw, duplicate, mirror, remix, or add any other logo, icon, wordmark, watermark, signature, or decorative brand mark.",
                "  - If the brand name appears in headline/body copy, treat it as ordinary text only; do not style it as a second logo.",
            ]
        )
    else:
        parts.append(
            "  - No brand logo was provided. Do NOT invent any logo, icon, wordmark, watermark, signature, or decorative brand mark."
        )
    if has_qr:
        parts.append(
            "  - A QR code: place it cleanly in the bottom-right area. "
            "Render the QR pixel-faithfully — do not stylize or recolor it; preserve its original black-and-white pattern so it remains scannable."
        )
    parts.extend(
        [
            "",
            "Render the following Chinese text exactly as written, with crisp, high-contrast typography that matches the reference style:",
            *[f"  {line}" for line in text_lines],
            "",
            f"Composition guidance: {template.composition_brief}",
        ]
    )
    return "\n".join(parts)


async def _load_reference_poster_bytes(
    storage: LocalStorageAdapter, posters: list
) -> list[bytes]:
    """Load reference posters and shrink them before handing to the model.

    Original poster files in the asset library are often 1-12 MB (tall
    9:16 PNGs). Sending them raw to ``/v1/images/edits`` blows the
    request size out (multipart) and burns provider tokens for no
    quality gain — the model only needs enough resolution to read the
    palette / composition / typography, not pixel-perfect detail.
    """
    out: list[bytes] = []
    for asset in posters:
        if not getattr(asset, "storage_uri", None):
            continue
        try:
            raw = await storage.get_file(asset.storage_uri)
        except Exception as e:
            logger.warning("Reference poster load failed (%s): %s", asset.id, e)
            continue
        shrunk = _shrink_for_provider(raw)
        out.append(shrunk if shrunk is not None else raw)
    return out


def _shrink_for_provider(raw: bytes, *, max_side: int = 1536) -> bytes | None:
    """PIL re-encode → JPEG quality 85, longest side <= max_side.

    Returns the smaller of (original, recoded) — if shrinkage doesn't
    help (e.g. tiny logo already), keep the original to preserve any
    transparency / sharpness. Returns None on decode failure so the
    caller can fall back to the original bytes.
    """
    import io as _io

    from PIL import Image as _PILImage

    try:
        with _PILImage.open(_io.BytesIO(raw)) as img:
            img.load()
            w, h = img.size
            longest = max(w, h)
            if longest <= max_side and len(raw) <= 600_000:
                return None  # already small enough; original is fine
            scale = max_side / float(longest) if longest > max_side else 1.0
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            resized = img.convert("RGB").resize(
                (new_w, new_h), _PILImage.LANCZOS
            )
            buf = _io.BytesIO()
            resized.save(buf, format="JPEG", quality=85, optimize=True)
            recoded = buf.getvalue()
            return recoded if len(recoded) < len(raw) else None
    except Exception as e:
        logger.debug("Reference shrink skipped: %s", e)
        return None


# ── Brief-first flow (trust-the-model) ─────────────────────────────


_ASPECT_HINTS_ZH: dict[str, str] = {
    "9:16": "vertical 9:16 mobile-story format",
    "1:1": "square 1:1 social-feed format",
    "16:9": "horizontal 16:9 wide format",
    "4:3": "horizontal 4:3 article-cover format",
    "3:4": "vertical 3:4 portrait format",
    "4:5": "vertical 4:5 portrait format",
}


async def create_brief_job(
    db: AsyncSession,
    data: BriefJobCreate,
) -> ImageJobResponse:
    """Brief-first poster generation.

    No templates, no slot system. Takes a free-form creative brief plus
    a curated reference set and lets the model do the composition.

    Falls back to ``generate_image`` (text-only) when the proxy doesn't
    expose ``/v1/images/edits`` — the fallback render is intentionally
    minimal: AI background plate with the brief's intent in the prompt
    plus a logo paste. We don't try to recreate the model's full
    composition with PIL because the brief has no schema for it.
    """
    try:
        offer_uuid = uuid.UUID(data.offer_id)
    except (ValueError, TypeError) as e:
        raise AppError("INVALID_OFFER_ID", "offer_id is not a valid UUID", 400) from e
    offer = await db.get(Offer, offer_uuid)
    if not offer:
        raise NotFoundError("Offer", data.offer_id)

    brandkit = await _get_offer_brandkit(db, offer_uuid)
    provider, provider_cfg_id, source_label, resolved_model_code = await _resolve_image_provider(
        db,
        provider_config_id=data.image_provider_config_id,
        model_code=data.image_model_code,
    )

    job = ImageGenerationJob(
        mode="poster",
        offer_id=offer_uuid,
        brandkit_id=brandkit.id if brandkit else None,
        template_id=None,  # brief flow has no template
        provider=provider.provider_name,
        provider_config_id=provider_cfg_id,
        status="pending",
        params={
            "brief": data.brief,
            "aspect_ratio": data.aspect_ratio,
            "reference_asset_ids": data.reference_asset_ids,
            "extra_asset_ids": data.extra_asset_ids,
            "extra_uploads": [u.model_dump() for u in data.extra_uploads],
            "qr_asset_id": data.qr_asset_id,
            "qr_asset_uri": data.qr_asset_uri,
            "image_provider_config_id": data.image_provider_config_id,
            "image_model_code": resolved_model_code,
            "provider_source": source_label,
            "flow": "brief",
        },
    )
    db.add(job)
    await db.flush()
    await db.commit()
    await db.refresh(job)

    job.status = "processing"
    job.started_at = _utcnow()
    await db.commit()

    storage = LocalStorageAdapter()

    try:
        # Resolve reference assets — user-curated, with auto-fill when empty.
        ref_assets = await _resolve_reference_assets(
            db,
            offer_id=offer_uuid,
            brief=data.brief,
            user_picked_ids=data.reference_asset_ids,
            limit=3,
        )
        ref_bytes = await _load_reference_poster_bytes(storage, ref_assets)

        # Extra assets — additional supporting references the user attached.
        extra_assets = await _load_assets_by_ids(
            db, data.extra_asset_ids, offer_id=offer_uuid
        )
        extra_bytes = await _load_reference_poster_bytes(storage, extra_assets)
        extra_upload_bytes, qr_upload_bytes, extra_uploads_used = (
            await _load_reference_upload_bytes(
                storage,
                data.extra_uploads,
                offer_id=offer_uuid,
            )
        )

        logo_bytes = await _load_logo_bytes(db, brandkit, storage)
        qr_bytes = await _load_brief_qr_bytes(data, storage, db=db, offer_id=offer_uuid)

        prompt = await _build_brief_prompt(
            brief=data.brief,
            offer=offer,
            brandkit=brandkit,
            aspect=data.aspect_ratio,
            has_logo=bool(logo_bytes),
            has_qr=bool(qr_bytes or qr_upload_bytes),
            extra_count=len(extra_bytes) + len(extra_upload_bytes),
        )
        # Append the user's persisted preferences for this offer's
        # image surface. The block is empty when there are no entries
        # — see render_memories_block. Suffix position is intentional:
        # the model treats the last instruction as the strictest, and
        # the header explicitly says "this section wins on conflict".
        from app.application.memory_service import (
            list_memories_for_offer,
            render_memories_block,
        )
        memories = await list_memories_for_offer(
            db, offer_id=offer_uuid, surface="image"
        )
        prompt += render_memories_block(memories)

        # Build the references list — order matters for the model:
        # style references first, brand assets next, optional content last.
        references = list(ref_bytes) + list(extra_bytes) + list(extra_upload_bytes)
        if logo_bytes:
            references.append(logo_bytes)
        if qr_bytes:
            references.append(qr_bytes)
        references.extend(qr_upload_bytes)

        ai_composed_path_used = False
        final_image_bytes: bytes | None = None

        if references:
            try:
                edits_result = await provider.generate_with_references(
                    GenerateWithReferencesRequest(
                        prompt=prompt,
                        references=references,
                        aspect_ratio=data.aspect_ratio,  # type: ignore[arg-type]
                        quality="standard",
                    )
                )
                final_image_bytes = edits_result.image_bytes
                ai_composed_path_used = True
            except UnsupportedReferenceMode as e:
                logger.info(
                    "Edits path unsupported for brief job %s, falling back: %s",
                    job.id,
                    e,
                )

        if final_image_bytes is None:
            # Fallback: text-only generation. Without a template / slot
            # schema we can't reconstruct the model's intended layout
            # with PIL — we hand back the AI plate with logo overlay and
            # let the user iterate the brief.
            gen_result = await provider.generate_image(
                GenerateImageRequest(
                    prompt=prompt,
                    aspect_ratio=data.aspect_ratio,  # type: ignore[arg-type]
                    quality="standard",
                )
            )
            final_image_bytes = gen_result.image_bytes
            if logo_bytes:
                final_image_bytes = _overlay_logo(
                    final_image_bytes, logo_bytes, data.aspect_ratio
                )

        sub_path = f"generated_images/{offer_uuid}"
        storage_uri = await storage.save_file(
            final_image_bytes,
            file_name=f"{job.id}.png",
            sub_path=sub_path,
        )
        public_url = f"/uploads/{storage_uri}"

        job.image_url = public_url
        job.preview_url = public_url
        job.status = "completed"
        job.finished_at = _utcnow()
        job.params = {
            **(job.params or {}),
            "prompt": prompt,
            "render_path": "ai_composed" if ai_composed_path_used else "ai_plate_with_logo",
            "reference_asset_ids_resolved": [str(a.id) for a in ref_assets],
            "extra_uploads_resolved": extra_uploads_used,
        }
        await db.commit()
    except AppError as e:
        job.status = "failed"
        job.error_message = f"{e.code}: {e.message}"
        job.finished_at = _utcnow()
        await db.commit()
        raise
    except Exception as e:
        logger.exception("Brief poster generation failed for job %s", job.id)
        job.status = "failed"
        job.error_message = f"INTERNAL: {e}"
        job.finished_at = _utcnow()
        await db.commit()
        raise AppError("IMAGE_GENERATION_FAILED", str(e), 500) from e

    await db.refresh(job)
    return _to_response(job)


async def _build_brief_prompt(
    *,
    brief: str,
    offer: Offer,
    brandkit: BrandKit | None,
    aspect: str,
    has_logo: bool,
    has_qr: bool,
    extra_count: int,
) -> str:
    """Compose the prompt for the brief-first flow.

    The brief stays verbatim — that's the user's intent in their own
    words. Around it we layer a tight band of brand context and a few
    composition / faithfulness rules. We do NOT describe the desired
    layout; the model has reference images for that.
    """
    sp_points = (offer.core_selling_points_json or {}).get("points") or []
    audience = (offer.target_audience_json or {}).get("items") or []
    voice = (brandkit.brand_voice or "").strip() if brandkit else ""

    aspect_hint = _ASPECT_HINTS_ZH.get(aspect, f"{aspect} format")
    selling_points_line = ""
    if sp_points:
        selling_points_line = (
            "Brand selling points: " + " / ".join(str(p) for p in sp_points[:4]) + ".\n"
        )
    audience_line = ""
    if audience:
        audience_line = (
            "Target audience: " + " / ".join(str(a) for a in audience[:3]) + ".\n"
        )
    brand_tone_line = ""
    if voice:
        first = voice.split("。")[0].split(".")[0].strip()
        if first and len(first) <= 140:
            brand_tone_line = f"Brand tone: {first}.\n"

    extra_reference_line = ""
    if extra_count:
        extra_reference_line = (
            f"Additional reference image(s) ({extra_count}) follow the style references — "
            "incorporate the elements they show (product shots, mascots, etc.) where appropriate.\n"
        )

    if has_logo:
        logo_block = (
            "\nLogo discipline:\n"
            "- Use only the provided logo as the brand mark.\n"
            "- Place the provided logo exactly once in a single corner; do not place a second logo anywhere else.\n"
            "- Do not invent, redraw, duplicate, mirror, remix, or add any other logo, icon, wordmark, watermark, signature, or decorative brand mark.\n"
            "- The brand name may appear only as ordinary headline/body text when required by the brief; do not style it as a second logo.\n\n"
        )
    else:
        logo_block = (
            "\nNo logo reference was provided. Do not invent any logo, icon, wordmark, watermark, signature, or decorative brand mark.\n\n"
        )

    qr_block = ""
    if has_qr:
        qr_block = (
            "Include the QR code (provided) in the bottom-right area. "
            "Render the QR pixel-faithfully — do not stylize or recolor it; preserve its "
            "original black-and-white pattern so it remains scannable.\n\n"
        )

    template = await get_effective_prompt(
        "image.brief_template",
        _default_image_brief_template,
    )
    return _render_prompt_template(
        template,
        preset_key="image.brief_template",
        fallback_getter=_default_image_brief_template,
        context={
            "brief": brief.strip(),
            "aspect_hint": aspect_hint,
            "brand_name": offer.name,
            "selling_points_line": selling_points_line,
            "audience_line": audience_line,
            "brand_tone_line": brand_tone_line,
            "extra_reference_line": extra_reference_line,
            "logo_block": logo_block,
            "qr_block": qr_block,
        },
    )


async def _resolve_reference_assets(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID,
    brief: str,
    user_picked_ids: list[str],
    limit: int = 3,
) -> list[Asset]:
    """Pick reference assets for the brief flow.

    User-curated picks always win. When the user provides none, fall back
    to brief-keyword overlap on tags (subject / selling_point / scenario
    / campaign_type) — see ``suggest_brief_references`` for the scoring.
    """
    if user_picked_ids:
        return await _load_assets_by_ids(db, user_picked_ids, offer_id=offer_id)
    suggestions = await suggest_brief_references(
        db, offer_id=offer_id, brief=brief, limit=limit
    )
    if not suggestions:
        return []
    ids = [s.asset_id for s in suggestions[:limit]]
    return await _load_assets_by_ids(db, ids, offer_id=offer_id)


async def _load_assets_by_ids(
    db: AsyncSession,
    asset_ids: list[str],
    *,
    offer_id: uuid.UUID | None = None,
) -> list[Asset]:
    """Load image assets by id, optionally enforcing offer-scope membership.

    When ``offer_id`` is provided, assets that belong to a different
    offer (or no offer at all) are silently dropped. This prevents the
    image-gen API from reaching across offer boundaries — a defense
    against a malformed or malicious caller passing somebody else's
    asset_id and getting their brand material fed into the model.
    """
    if not asset_ids:
        return []
    out: list[Asset] = []
    for raw in asset_ids:
        try:
            asset_uuid = uuid.UUID(raw)
        except (ValueError, TypeError):
            continue
        asset = await db.get(Asset, asset_uuid)
        if not asset or asset.asset_type != "image":
            continue
        if offer_id is not None:
            if asset.scope_type != "offer" or asset.scope_id != offer_id:
                logger.warning(
                    "Cross-offer asset reference rejected: asset=%s scope=%s/%s expected=offer/%s",
                    asset.id,
                    asset.scope_type,
                    asset.scope_id,
                    offer_id,
                )
                continue
        out.append(asset)
    return out


async def _load_brief_qr_bytes(
    data: BriefJobCreate,
    storage: LocalStorageAdapter,
    *,
    db: AsyncSession,
    offer_id: uuid.UUID | None = None,
) -> bytes | None:
    """Load the optional QR-code reference, scope-checked.

    Two paths, in preference order:

      1. ``qr_asset_id`` — DB lookup, verify ``scope_type=='offer'`` and
         ``scope_id == offer_id``. Path-traversal-safe by construction
         (we never trust a caller-supplied path).

      2. ``qr_asset_uri`` (legacy) — accept only if ``os.path.normpath``
         leaves it strictly inside ``<offer_id>/``. Rejects
         ``offer_id/../other_offer/file`` and similar tricks.
    """
    # Path 1 — preferred.
    if data.qr_asset_id:
        try:
            asset_uuid = uuid.UUID(data.qr_asset_id)
        except (ValueError, TypeError):
            logger.warning("QR asset_id is not a valid UUID: %s", data.qr_asset_id)
            return None
        asset = await db.get(Asset, asset_uuid)
        if not asset or asset.asset_type != "image":
            return None
        if offer_id is not None and (
            asset.scope_type != "offer" or asset.scope_id != offer_id
        ):
            logger.warning(
                "Cross-offer QR asset rejected: asset=%s scope=%s/%s expected=offer/%s",
                asset.id,
                asset.scope_type,
                asset.scope_id,
                offer_id,
            )
            return None
        if not asset.storage_uri:
            return None
        try:
            return await storage.get_file(asset.storage_uri)
        except Exception as e:
            logger.warning("QR asset file load failed (%s): %s", asset.id, e)
            return None

    # Path 2 — legacy URI with hardened path check.
    if data.qr_asset_uri and offer_id is not None:
        normalized = _safe_offer_subpath(data.qr_asset_uri, offer_id)
        if not normalized:
            logger.warning(
                "Cross-offer QR uri rejected (normpath guard): uri=%s offer=%s",
                data.qr_asset_uri,
                offer_id,
            )
            return None
        try:
            return await storage.get_file(normalized)
        except Exception as e:
            logger.warning("Brief QR uri load failed (%s): %s", normalized, e)
            return None

    return None


async def _load_reference_upload_bytes(
    storage: LocalStorageAdapter,
    uploads: list[ReferenceUploadInput],
    *,
    offer_id: uuid.UUID,
) -> tuple[list[bytes], list[bytes], list[dict[str, Any]]]:
    """Load one-off uploads for a generation request.

    Returns ``(supplemental_bytes, qr_bytes, resolved_metadata)``. The
    upload_id is a storage URI created by ``save_reference_upload`` and
    must remain under ``tmp/image_refs/<offer_id>/`` after normalization.
    """
    supplemental: list[bytes] = []
    qr: list[bytes] = []
    resolved: list[dict[str, Any]] = []

    for item in uploads[:4]:
        normalized = _safe_reference_upload_subpath(item.upload_id, offer_id)
        if not normalized:
            logger.warning(
                "One-off reference upload rejected: upload_id=%s offer=%s",
                item.upload_id,
                offer_id,
            )
            continue
        try:
            raw = await storage.get_file(normalized)
        except Exception as e:
            logger.warning("One-off reference load failed (%s): %s", normalized, e)
            continue

        shrunk = _shrink_for_provider(raw)
        payload = shrunk if shrunk is not None else raw
        if item.role == "qr":
            qr.append(payload)
        else:
            supplemental.append(payload)
        # SERVER-DERIVED URL ONLY. We never echo back ``item.url`` — a
        # direct API caller could otherwise pass an arbitrary external
        # URL that would then show up in the job's "AI used these
        # materials" panel as if it were stored on our server. The
        # only trustworthy path is the one the boundary check
        # produced from the validated upload_id.
        resolved.append(
            {
                "upload_id": normalized,
                "role": item.role,
                "label": item.label,
                "url": f"/uploads/{normalized}",
            }
        )

    return supplemental, qr, resolved


def _safe_reference_upload_subpath(uri: str, offer_id: uuid.UUID) -> str | None:
    """Normalize and validate a one-off upload storage URI."""
    normalized = _normalize_storage_uri(uri)
    if not normalized:
        return None
    prefix = f"tmp/image_refs/{offer_id}/"
    if not normalized.startswith(prefix):
        return None
    return normalized


def _safe_offer_subpath(uri: str, offer_id: uuid.UUID) -> str | None:
    """Normalize a storage URI and confirm its FIRST path segment is
    the offer's id. Returns the normalized path on success, or None if
    the URI escapes the offer's subdirectory.

    Defends against path-traversal tricks like ``offer_id/../other_offer/file``,
    which startswith() would falsely accept but normpath collapses to
    ``other_offer/file``.
    """
    normalized = _normalize_storage_uri(uri)
    if not normalized:
        return None
    parts = normalized.split("/")
    if not parts or parts[0] != str(offer_id):
        return None
    return normalized


def _normalize_storage_uri(uri: str) -> str | None:
    import os.path as _op

    if not uri:
        return None
    cleaned = uri.replace("\\", "/").lstrip("/")
    normalized = _op.normpath(cleaned).replace("\\", "/")
    # Reject anything that climbs out (normpath leaves leading "../" if it can't resolve)
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        return None
    return normalized


def _overlay_logo(
    background_bytes: bytes, logo_bytes: bytes, aspect: str
) -> bytes:
    """Minimal logo overlay for the brief-flow fallback path.

    Pastes the logo top-left at 22% width with the contrast-aware recolor
    when the local area is dark. Used only when the model can't take
    image inputs — the edits path lets the model do this natively.
    """
    import io as _io

    from PIL import Image as _PILImage

    from app.application.image_template import (
        _recolor_dark_text_to_white,
        _sample_bg_luma,
    )

    try:
        canvas = _PILImage.open(_io.BytesIO(background_bytes)).convert("RGBA")
    except Exception:
        return background_bytes
    cw, ch = canvas.size
    logo_w = int(0.22 * cw)
    logo_h = int(0.10 * ch)
    cx = int(0.18 * cw)
    cy = int(0.06 * ch)
    sample_box = (
        max(0, cx - logo_w // 2),
        max(0, cy - logo_h // 2),
        min(cw, cx + logo_w // 2),
        min(ch, cy + logo_h // 2),
    )
    bg_luma = _sample_bg_luma(canvas, sample_box)
    final_logo = (
        _recolor_dark_text_to_white(logo_bytes) if bg_luma < 110 else logo_bytes
    )

    try:
        logo_img = _PILImage.open(_io.BytesIO(final_logo)).convert("RGBA")
    except Exception:
        return background_bytes
    logo_img.thumbnail((logo_w, logo_h), _PILImage.LANCZOS)
    iw, ih = logo_img.size
    canvas.alpha_composite(logo_img, (cx - iw // 2, cy - ih // 2))

    out = _io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


# ── Reference suggestion (brief-keyword × tag overlap) ─────────────


_BRIEF_STOPWORDS = {
    "的", "了", "是", "和", "或", "在", "我", "你", "他", "我们", "你们", "他们",
    "做", "一张", "一个", "一份", "需要", "想要", "希望", "请", "帮", "搞", "弄",
    "海报", "图片", "图", "宣发", "发", "画", "生成",
    "with", "for", "and", "or", "the", "a", "an", "of", "to",
}


async def suggest_brief_references(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID,
    brief: str,
    limit: int = 3,
) -> list[ReferenceSuggestion]:
    """Score offer's image assets by overlap between brief tokens and tags.

    Cheap, deterministic, no LLM call. Tokenization is naive — strips
    stopwords and splits on Chinese-friendly delimiters. Good enough as a
    first-pass; LLM-based picking can land later as an opt-in upgrade.
    """
    tokens = _tokenize_brief(brief)
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
        .limit(30)
    )
    candidates = (await db.execute(base_q)).scalars().all()

    scored: list[ReferenceSuggestion] = []
    for asset in candidates:
        meta = asset.metadata_json or {}
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        if w < 600 and h < 600:
            continue  # skip logo-sized images; they pollute style anchoring
        tags = asset.tags_json or {}
        score, reason = _score_brief_match(tokens, tags, asset.hook_score)
        if score <= 0:
            continue
        scored.append(
            ReferenceSuggestion(
                asset_id=str(asset.id),
                score=score,
                reason=reason,
            )
        )

    if not scored:
        # Brief had no usable tokens (or no tag matches) — fall back to
        # top-N by hook_score so the user always sees suggestions.
        for asset in candidates[:limit]:
            meta = asset.metadata_json or {}
            w = meta.get("width") or 0
            h = meta.get("height") or 0
            if w < 600 and h < 600:
                continue
            scored.append(
                ReferenceSuggestion(
                    asset_id=str(asset.id),
                    score=float(asset.hook_score or 0),
                    reason="按 hook_score 推荐",
                )
            )

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:limit]


def _tokenize_brief(brief: str) -> list[str]:
    """Minimal brief tokenizer — splits on whitespace + Chinese punctuation
    and drops short / stop-word tokens. Not jieba-perfect, but enough to
    surface terms like 「数字人」「蝉镜」「招募」「公众号」「投放」."""
    if not brief:
        return []
    cleaned = brief
    for sep in "，。、；：！？,.;:!?\n\t ()（）「」〈〉《》":
        cleaned = cleaned.replace(sep, " ")
    raw = [t.strip() for t in cleaned.split(" ") if t.strip()]
    return [t for t in raw if len(t) >= 2 and t not in _BRIEF_STOPWORDS]


def _score_brief_match(
    tokens: list[str], tags: dict, hook_score: float | None
) -> tuple[float, str]:
    if not tokens:
        return 0.0, ""
    matches: dict[str, list[str]] = {}
    bag: list[str] = []
    for field in ("subject", "selling_point", "scenario", "campaign_type", "usage", "channel_fit"):
        for t in tags.get(field, []) or []:
            if isinstance(t, str):
                bag.append(t)

    for token in tokens:
        for tag in bag:
            if token in tag or tag in token:
                matches.setdefault(token, []).append(tag)
                break

    if not matches:
        return 0.0, ""

    score = float(len(matches)) + 0.4 * float(hook_score or 0)
    sample = next(iter(matches.values()))[0]
    reason = f"匹配「{sample}」+{len(matches)} 个关键词"
    return score, reason


# ── Refine (iterative single-image edit) ──────────────────────────


async def create_refine_job(
    db: AsyncSession,
    parent_job_id: uuid.UUID,
    data: RefineJobCreate,
) -> ImageJobResponse:
    """Refinement turn on a previously-generated image.

    The parent image bytes are passed as the primary visual reference
    to the model along with a short instruction ("make the logo bigger",
    "swap the background to cool tones"). Original brief / offer /
    brandkit context are inherited from the parent so the refinement
    stays anchored to the same brand intent.

    Each call creates a NEW ImageGenerationJob row — the parent stays
    intact so the user can fork from any earlier version.
    """
    parent = await db.get(ImageGenerationJob, parent_job_id)
    if not parent:
        raise NotFoundError("ImageGenerationJob", str(parent_job_id))
    if parent.status != "completed" or not parent.image_url:
        raise AppError(
            "PARENT_NOT_COMPLETED",
            "Cannot refine: parent job is not completed",
            400,
        )
    if not parent.image_url.startswith("/uploads/"):
        raise AppError(
            "PARENT_IMAGE_UNREACHABLE",
            "Parent image is not stored locally",
            400,
        )

    parent_params = parent.params or {}
    parent_brief = parent_params.get("brief") or ""
    lineage_root = parent_params.get("lineage_root") or str(parent.id)

    offer = await db.get(Offer, parent.offer_id) if parent.offer_id else None
    brandkit = (
        await _get_offer_brandkit(db, parent.offer_id) if parent.offer_id else None
    )
    # Inherit the parent's model so a refinement chain stays on the same
    # model — switching mid-lineage would let the new turn redraw the
    # image in a different style and break the "edit, don't replace"
    # contract the user expects.
    inherited_pcid = parent_params.get("image_provider_config_id")
    inherited_model = parent_params.get("image_model_code")
    provider, provider_cfg_id, source_label, resolved_model_code = await _resolve_image_provider(
        db,
        provider_config_id=inherited_pcid,
        model_code=inherited_model,
    )
    aspect = parent_params.get("aspect_ratio") or "9:16"

    job = ImageGenerationJob(
        mode=parent.mode,
        offer_id=parent.offer_id,
        brandkit_id=parent.brandkit_id,
        creation_id=parent.creation_id,
        template_id=None,
        provider=provider.provider_name,
        provider_config_id=provider_cfg_id,
        status="pending",
        params={
            "flow": "refine",
            "refinement": data.refinement,
            "parent_job_id": str(parent.id),
            "lineage_root": lineage_root,
            "brief": parent_brief,
            "aspect_ratio": aspect,
            "image_provider_config_id": inherited_pcid,
            "image_model_code": resolved_model_code,
        },
    )
    db.add(job)
    await db.flush()
    await db.commit()
    await db.refresh(job)

    job.status = "processing"
    job.started_at = _utcnow()
    await db.commit()

    storage = LocalStorageAdapter()

    try:
        parent_uri = parent.image_url[len("/uploads/"):]
        try:
            parent_bytes = await storage.get_file(parent_uri)
        except Exception as e:
            raise AppError(
                "PARENT_IMAGE_LOAD_FAILED",
                f"Could not read parent image: {e}",
                500,
            ) from e

        logo_bytes = None
        if brandkit:
            logo_bytes = await _load_logo_bytes(db, brandkit, storage)

        prompt = await _build_refine_prompt(
            refinement=data.refinement,
            parent_brief=parent_brief,
            offer=offer,
            aspect=aspect,
        )
        # Refines also fold in offer memories. Critical: without this,
        # users who saved "不要红色" via the cover panel and then
        # refined the image would still be able to introduce red at
        # refine time — the parent prompt didn't carry the constraint
        # and the refine prompt is built fresh. Surface='image' for
        # both poster (parent.mode='poster') and article-cover refines.
        if parent.offer_id:
            from app.application.memory_service import (
                list_memories_for_offer,
                render_memories_block,
            )
            memories = await list_memories_for_offer(
                db, offer_id=parent.offer_id, surface="image"
            )
            prompt += render_memories_block(memories)

        # Order: parent image FIRST (primary edit target). Logo last as
        # supporting brand reference — model will keep it consistent.
        references: list[bytes] = [parent_bytes]
        if logo_bytes:
            references.append(logo_bytes)

        ai_composed_path_used = False
        final_image_bytes: bytes | None = None

        try:
            edits_result = await provider.generate_with_references(
                GenerateWithReferencesRequest(
                    prompt=prompt,
                    references=references,
                    aspect_ratio=aspect,  # type: ignore[arg-type]
                    quality="standard",
                )
            )
            final_image_bytes = edits_result.image_bytes
            ai_composed_path_used = True
        except UnsupportedReferenceMode as e:
            # The /v1/images/edits call failed. This can be either:
            #   (a) the proxy genuinely doesn't support image inputs, OR
            #   (b) a transient SSL/connection flap (we've seen the same
            #       proxy serve /edits successfully minutes earlier).
            # Refine has no meaningful text-only fallback — surface a
            # message that flags both possibilities so the user knows to
            # try again once before reaching for a config change.
            raise AppError(
                "REFINE_EDITS_UNAVAILABLE",
                "改图调用 /v1/images/edits 失败。可能是代理临时不通（再点一次试试）；"
                "如果反复失败，需要换一个支持 image input 的 OpenAI 通道（真 key / "
                "其他兼容代理）。原始错误：" + str(e),
                502,
            ) from e

        # Refines of article covers inherit the parent's aspect; if
        # that aspect was wide (2.35:1 / 1.91:1) the provider returned
        # a closest-landscape render, same as the original cover job.
        # Apply the same crop so v2 stays at the user's target ratio
        # and the ``creation.cover_image_url`` write below points to a
        # correctly-sized image.
        if parent.mode == "article_cover":
            final_image_bytes = _crop_image_to_aspect(
                final_image_bytes, aspect
            )

        sub_path = (
            f"generated_images/{parent.offer_id}"
            if parent.offer_id
            else f"generated_images/refine/{lineage_root}"
        )
        storage_uri = await storage.save_file(
            final_image_bytes,
            file_name=f"{job.id}.png",
            sub_path=sub_path,
        )
        public_url = f"/uploads/{storage_uri}"

        job.image_url = public_url
        job.preview_url = public_url
        job.status = "completed"
        job.finished_at = _utcnow()
        job.params = {
            **(job.params or {}),
            "prompt": prompt,
            "render_path": "ai_composed" if ai_composed_path_used else "ai_plate_with_logo",
        }
        # When the lineage is anchored to an article (mode='article_cover'),
        # the latest version is what content-studio shows as the cover —
        # carry the refine result forward so the article thumb tracks the
        # current end of the chain instead of staying stuck on v1.
        if parent.mode == "article_cover" and parent.creation_id:
            target = await db.get(Creation, parent.creation_id)
            if target is not None:
                target.cover_image_url = public_url
        await db.commit()
    except AppError as e:
        job.status = "failed"
        job.error_message = f"{e.code}: {e.message}"
        job.finished_at = _utcnow()
        await db.commit()
        raise
    except Exception as e:
        logger.exception("Refine generation failed for job %s", job.id)
        job.status = "failed"
        job.error_message = f"INTERNAL: {e}"
        job.finished_at = _utcnow()
        await db.commit()
        raise AppError("IMAGE_GENERATION_FAILED", str(e), 500) from e

    await db.refresh(job)
    return _to_response(job)


async def _build_refine_prompt(
    *,
    refinement: str,
    parent_brief: str,
    offer: Offer | None,
    aspect: str,
) -> str:
    """Compose the prompt for a refinement turn.

    Critical contract: the model must edit the provided image, not
    generate a new one. We restate the original brief as anchoring
    context so the refinement doesn't drift away from the user's
    initial intent.
    """
    aspect_hint = _ASPECT_HINTS_ZH.get(aspect, f"{aspect} format")
    brand_name = offer.name if offer else ""
    brand_line = f"Brand: {brand_name}.\n" if brand_name else ""
    template = await get_effective_prompt(
        "image.refine_template",
        _default_image_refine_template,
    )
    return _render_prompt_template(
        template,
        preset_key="image.refine_template",
        fallback_getter=_default_image_refine_template,
        context={
            "refinement": refinement.strip(),
            "parent_brief": parent_brief.strip() or "(unspecified)",
            "brand_name": brand_name,
            "brand_line": brand_line,
            "aspect_hint": aspect_hint,
        },
    )


async def list_lineage(
    db: AsyncSession, job_id: uuid.UUID
) -> list[ImageJobResponse]:
    """Return every job sharing the same lineage root, oldest first.

    The "lineage" is the v1 → v2 → v3 refinement chain. v1's lineage
    root is its own id; subsequent refinements carry it through.
    """
    job = await db.get(ImageGenerationJob, job_id)
    if not job:
        raise NotFoundError("ImageGenerationJob", str(job_id))
    root_id = (job.params or {}).get("lineage_root") or str(job.id)
    rows = (
        await db.execute(
            select(ImageGenerationJob)
            .where(ImageGenerationJob.id == uuid.UUID(root_id))
        )
    ).scalars().all()
    # Plus all jobs where params.lineage_root equals root_id.
    descendants_q = (
        select(ImageGenerationJob)
        .where(ImageGenerationJob.params["lineage_root"].astext == root_id)
        .order_by(ImageGenerationJob.created_at.asc())
    )
    descendants = (await db.execute(descendants_q)).scalars().all()
    seen = {str(j.id) for j in rows}
    for j in descendants:
        if str(j.id) not in seen:
            rows.append(j)
            seen.add(str(j.id))
    rows.sort(key=lambda r: r.created_at)
    return [_to_response(r) for r in rows]


# ── Article-cover flow ──────────────────────────────────────────────


# ── Article cover: aspect-by-platform + LLM-derived suggestion ──────


# Cover-image aspect ratios per publishing platform.
#
# These are CHOSEN for cover thumbnails specifically — distinct from the
# script-platform ``aspect_ratio`` field (portrait/landscape/square) which
# describes video-content shape. Article platforms (linkedin / blog /
# wechat_gzh) have no native cover-aspect metadata, so we encode the
# practical defaults publishers use here.
#
# Sources:
#   - 公众号 (wechat_gzh): 2.35:1 long horizontal title image, the de-facto
#     标头图 standard.
#   - 小红书 (xiaohongshu): 3:4 vertical card matches the feed's portrait
#     thumbnail; 4:5 also acceptable but 3:4 packs better with text overlay.
#   - LinkedIn / Substack: 1.91:1 OG cards (1200×627) — what their
#     aggregators / embeds pick up.
#   - X (twitter) / blog / Discord: 16:9 broad-default.
#   - Instagram carousel: 4:5 portrait (highest engagement; 1:1 also
#     works but loses real estate vs 4:5).
#   - Reddit: 4:3 thumbnail.
#   - Video platforms (douyin / tiktok / wechat_video / youtube_shorts):
#     content-studio's platform list filters these out, but we keep the
#     mapping so direct API calls still get a sensible answer.
_COVER_ASPECT_BY_PLATFORM: dict[str, str] = {
    # zh
    "wechat_gzh":         "2.35:1",
    "xiaohongshu":        "3:4",
    "wechat_video":       "9:16",
    "douyin":             "9:16",
    # en
    "linkedin":           "1.91:1",
    "substack":           "1.91:1",
    "blog":               "16:9",
    "x_twitter":          "16:9",
    "instagram_carousel": "4:5",
    "reddit":             "4:3",
    "discord":            "16:9",
    "tiktok":             "9:16",
    "youtube_shorts":     "9:16",
}
_DEFAULT_COVER_ASPECT = "16:9"


def _aspect_for_platform(platform_id: str | None) -> str:
    if not platform_id:
        return _DEFAULT_COVER_ASPECT
    return _COVER_ASPECT_BY_PLATFORM.get(platform_id, _DEFAULT_COVER_ASPECT)


def _aspect_string_to_ratio(aspect: str) -> float | None:
    """Parse an aspect string like ``2.35:1`` → 2.35. Returns None if
    the string isn't of the ``W:H`` form so callers can no-op rather
    than crash."""
    try:
        w_str, h_str = aspect.split(":")
        w, h = float(w_str), float(h_str)
        if h <= 0:
            return None
        return w / h
    except (ValueError, AttributeError):
        return None


def _crop_image_to_aspect(image_bytes: bytes, target_aspect: str) -> bytes:
    """Center-crop image bytes to a target aspect ratio.

    Why this exists: the image providers (OpenAI gpt-image, Gemini,
    Chanjing) only render a small fixed set of aspects (1:1, 16:9,
    3:2, etc.). Article-cover platforms ask for ratios providers
    don't natively support — most importantly 2.35:1 (公众号 long
    horizontal title image) and 1.91:1 (LinkedIn / Substack OG card).
    Adapter layer routes those to the closest landscape (16:9 / 3:2);
    this helper crops the result to the exact ratio before save so
    users get what they asked for.

    Idempotent: when source already matches target within 1%, returns
    bytes unchanged. Handles both too-wide (crop sides) and too-tall
    (crop top+bottom) sources via center crop. Falls through with the
    original bytes on any PIL error so a crop bug never blocks an
    otherwise-completed render.
    """
    target = _aspect_string_to_ratio(target_aspect)
    if target is None or target <= 0:
        return image_bytes

    try:
        from PIL import Image
    except Exception:
        return image_bytes

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception:
        return image_bytes

    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return image_bytes
    src_ratio = src_w / src_h
    if abs(src_ratio - target) / target < 0.01:
        return image_bytes

    if src_ratio > target:
        new_w = max(1, int(round(src_h * target)))
        left = (src_w - new_w) // 2
        cropped = img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = max(1, int(round(src_w / target)))
        top = (src_h - new_h) // 2
        cropped = img.crop((0, top, src_w, top + new_h))

    out = io.BytesIO()
    fmt = (img.format or "PNG").upper()
    save_format = "PNG" if fmt not in {"PNG", "JPEG", "WEBP"} else fmt
    if save_format == "JPEG" and cropped.mode in ("RGBA", "P"):
        cropped = cropped.convert("RGB")
    try:
        cropped.save(out, format=save_format)
    except Exception:
        return image_bytes
    return out.getvalue()


async def _suggest_assets_by_tags(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID,
    tags: list[str],
    limit: int = 2,
) -> list[uuid.UUID]:
    """Pick offer-scoped assets whose tag set overlaps the given tags.

    Stricter than ``suggest_brief_references``: no hook_score fallback —
    if no asset's tags overlap, return [] rather than serving an
    arbitrary "top by hook_score" result. The cover panel surfaces
    these as auto-selected; we'd rather select nothing than mislead.
    """
    if not tags:
        return []
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
        .limit(30)
    )
    candidates = (await db.execute(base_q)).scalars().all()
    scored: list[tuple[float, uuid.UUID]] = []
    for asset in candidates:
        meta = asset.metadata_json or {}
        if (meta.get("width") or 0) < 600 and (meta.get("height") or 0) < 600:
            continue
        score, _ = _score_brief_match(tags, asset.tags_json or {}, asset.hook_score)
        if score > 0:
            scored.append((score, asset.id))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [aid for _, aid in scored[:limit]]


async def _build_cover_derive_prompt(
    *,
    title: str,
    platform: str,
    body: str,
) -> str:
    template = await get_effective_prompt(
        "image.cover_derive",
        _default_cover_derive_prompt,
    )
    return _render_prompt_template(
        template,
        preset_key="image.cover_derive",
        fallback_getter=_default_cover_derive_prompt,
        context={
            "title": title,
            "platform": platform,
            "body": body,
        },
    )


async def derive_article_cover_suggestion(
    db: AsyncSession,
    creation_id: uuid.UUID,
    platform_id: str | None,
) -> CoverSuggestionResponse:
    """LLM-derive a one-shot cover brief + visual tags from the article.

    Single LLM call returns brief + tags — both in the article's
    language so tag overlap against the user's asset-library tags
    actually works (the user labelled their assets in their own
    language). UI chrome stays in the UI locale; brief / tags / any
    text the model renders later follow the source.

    Degrades gracefully: if no LLM is configured or the call fails,
    returns an empty CoverSuggestionResponse with the platform-derived
    aspect — the panel can still show a usable empty form.
    """
    creation = await db.get(Creation, creation_id)
    if not creation:
        raise NotFoundError("Creation", str(creation_id))

    aspect = _aspect_for_platform(platform_id)
    fallback = CoverSuggestionResponse(
        brief="",
        tags=[],
        suggested_asset_ids=[],
        aspect_ratio=aspect,  # type: ignore[arg-type]
    )

    llm = await style_extractor._get_active_openai_llm(db)
    if not llm or not llm.api_key:
        return fallback

    try:
        from openai import AsyncOpenAI
    except Exception as e:
        logger.warning("openai SDK not available for cover-suggest: %s", e)
        return fallback

    title = (creation.title or "").strip()
    body_excerpt = (creation.content or "").strip()[:800]
    prompt = await _build_cover_derive_prompt(
        title=title or "(untitled)",
        platform=platform_id or "unspecified",
        body=body_excerpt or "(empty)",
    )

    client = AsyncOpenAI(
        api_key=llm.api_key,
        base_url=(llm.base_url or "").strip() or None,
        timeout=30.0,
    )
    try:
        resp = await client.chat.completions.create(
            model=llm.model_name or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.5,
        )
        raw = (resp.choices[0].message.content or "{}").strip()
    except Exception as e:
        logger.warning(
            "Cover-suggest LLM call failed for creation %s: %s", creation_id, e
        )
        return fallback

    import json
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning(
            "Cover-suggest LLM returned non-JSON for creation %s: %r",
            creation_id,
            raw[:200],
        )
        return fallback

    brief = str(data.get("brief") or "").strip()[:500]
    tags = [
        str(t).strip()
        for t in (data.get("tags") or [])
        if isinstance(t, (str, int)) and str(t).strip()
    ][:5]

    suggested_ids: list[str] = []
    if creation.offer_id and tags:
        ids = await _suggest_assets_by_tags(
            db, offer_id=creation.offer_id, tags=tags, limit=2
        )
        suggested_ids = [str(i) for i in ids]

    return CoverSuggestionResponse(
        brief=brief,
        tags=tags,
        suggested_asset_ids=suggested_ids,
        aspect_ratio=aspect,  # type: ignore[arg-type]
    )


async def create_article_cover_job(
    db: AsyncSession,
    creation_id: uuid.UUID,
    data: ArticleCoverJobCreate,
) -> ImageJobResponse:
    """Generate a cover image for an article-style Creation.

    Two modes share this entry, picked by whether ``data.brief`` is set:

      - **Brief-first** (preferred, used by the content-studio cover
        panel): caller provides ``brief`` + a curated reference set
        (``reference_asset_ids`` / ``extra_asset_ids`` / ``extra_uploads``).
        We feed reference images to the model so the cover stays
        visually consistent with the brand kit and the article tone.

      - **Light path** (legacy callers): no brief, no references.
        The server auto-builds a prompt from article title + body +
        offer hints. This stays for backward compatibility with
        clients that haven't adopted the new schema fields.

    Refines (v1 → v2) go through ``POST /image-jobs/{id}/refine`` —
    that endpoint inherits ``mode`` + ``creation_id`` from the parent
    so the v2 stays linked to the article without duplicate logic here.
    """
    creation = await db.get(Creation, creation_id)
    if not creation:
        raise NotFoundError("Creation", str(creation_id))

    offer = (
        await db.get(Offer, creation.offer_id) if creation.offer_id else None
    )
    brandkit = (
        await _get_offer_brandkit(db, creation.offer_id)
        if creation.offer_id
        else None
    )

    provider, provider_cfg_id, source_label, resolved_model_code = (
        await _resolve_image_provider(
            db,
            provider_config_id=data.image_provider_config_id,
            model_code=data.image_model_code,
        )
    )

    brief_text = (data.brief or "").strip()
    extra_text = (data.extra_prompt or "").strip()
    use_brief_path = bool(brief_text)

    job = ImageGenerationJob(
        mode="article_cover",
        creation_id=creation_id,
        offer_id=creation.offer_id,
        brandkit_id=brandkit.id if brandkit else None,
        provider=provider.provider_name,
        provider_config_id=provider_cfg_id,
        status="pending",
        params={
            "aspect_ratio": data.aspect_ratio,
            "brief": brief_text,
            "extra_prompt": extra_text,
            "reference_asset_ids": data.reference_asset_ids,
            "extra_asset_ids": data.extra_asset_ids,
            "extra_uploads": [u.model_dump() for u in data.extra_uploads],
            "image_provider_config_id": data.image_provider_config_id,
            "image_model_code": resolved_model_code,
            "provider_source": source_label,
            "flow": "article_cover_brief" if use_brief_path else "article_cover_light",
        },
    )
    db.add(job)
    await db.flush()
    await db.commit()
    await db.refresh(job)

    job.status = "processing"
    job.started_at = _utcnow()
    await db.commit()

    storage = LocalStorageAdapter()

    try:
        if use_brief_path and creation.offer_id and offer is not None:
            # Brief-first path — same composition contract as
            # create_brief_job, minus QR (covers don't carry QR codes).
            ref_assets = await _resolve_reference_assets(
                db,
                offer_id=creation.offer_id,
                brief=brief_text,
                user_picked_ids=data.reference_asset_ids,
                limit=3,
            )
            ref_bytes = await _load_reference_poster_bytes(storage, ref_assets)

            extra_assets = await _load_assets_by_ids(
                db, data.extra_asset_ids, offer_id=creation.offer_id
            )
            extra_bytes = await _load_reference_poster_bytes(storage, extra_assets)
            extra_upload_bytes, _qr_unused, extra_uploads_used = (
                await _load_reference_upload_bytes(
                    storage,
                    data.extra_uploads,
                    offer_id=creation.offer_id,
                )
            )

            logo_bytes = await _load_logo_bytes(db, brandkit, storage)

            prompt = await _build_brief_prompt(
                brief=brief_text,
                offer=offer,
                brandkit=brandkit,
                aspect=data.aspect_ratio,
                has_logo=bool(logo_bytes),
                has_qr=False,
                extra_count=len(extra_bytes) + len(extra_upload_bytes),
            )
            if extra_text:
                prompt += f"\nStyle note: {extra_text}"
            # Article-cover memories live on surface='image' (the
            # generator class), not 'content' — the latter is for
            # script/post text rules. Same retrieval contract as
            # create_brief_job above.
            from app.application.memory_service import (
                list_memories_for_offer,
                render_memories_block,
            )
            memories = await list_memories_for_offer(
                db, offer_id=creation.offer_id, surface="image"
            )
            prompt += render_memories_block(memories)

            references = list(ref_bytes) + list(extra_bytes) + list(extra_upload_bytes)
            if logo_bytes:
                references.append(logo_bytes)

            ai_composed_path_used = False
            final_image_bytes: bytes | None = None

            if references:
                try:
                    edits_result = await provider.generate_with_references(
                        GenerateWithReferencesRequest(
                            prompt=prompt,
                            references=references,
                            aspect_ratio=data.aspect_ratio,  # type: ignore[arg-type]
                            quality="standard",
                        )
                    )
                    final_image_bytes = edits_result.image_bytes
                    ai_composed_path_used = True
                except UnsupportedReferenceMode as e:
                    logger.info(
                        "Edits path unsupported for cover job %s, falling back: %s",
                        job.id,
                        e,
                    )

            if final_image_bytes is None:
                gen_result = await provider.generate_image(
                    GenerateImageRequest(
                        prompt=prompt,
                        aspect_ratio=data.aspect_ratio,  # type: ignore[arg-type]
                        quality="standard",
                    )
                )
                final_image_bytes = gen_result.image_bytes

            job.params = {
                **(job.params or {}),
                "prompt": prompt,
                "render_path": "ai_composed" if ai_composed_path_used else "ai_plate",
                "reference_asset_ids_resolved": [str(a.id) for a in ref_assets],
                "extra_uploads_resolved": extra_uploads_used,
            }
        else:
            # Light path — title + body + offer hints, no reference
            # images. Preserved for legacy API consumers.
            title = (creation.title or "").strip()
            body = (creation.content or "").strip()
            body_excerpt = body[:280]
            offer_hints: list[str] = []
            if offer and offer.core_selling_points_json:
                points = (offer.core_selling_points_json or {}).get("points") or []
                offer_hints.extend([str(p) for p in points[:3]])

            prompt_parts = [
                f"Editorial article cover image, {data.aspect_ratio} aspect ratio.",
                f"Article title: {title}." if title else "",
                f"Article excerpt: {body_excerpt}" if body_excerpt else "",
                f"Brand context: {' | '.join(offer_hints)}" if offer_hints else "",
                f"Style note: {extra_text}" if extra_text else "",
                "Photographic editorial mood, clean composition, minimal text-friendly negative space.",
                "DO NOT include any text, letters, or watermarks in the image.",
            ]
            prompt = " ".join(p for p in prompt_parts if p)

            result = await provider.generate_image(
                GenerateImageRequest(
                    prompt=prompt,
                    aspect_ratio=data.aspect_ratio,  # type: ignore[arg-type]
                    quality="standard",
                )
            )
            final_image_bytes = result.image_bytes
            job.params = {
                **(job.params or {}),
                "prompt": prompt,
                "render_path": "light",
            }

        # Enforce target aspect — providers only render ~7 aspects, so
        # wide article-cover ratios (2.35:1 公众号, 1.91:1 LinkedIn)
        # come back at the closest landscape (16:9 / 3:2). Crop them
        # to the exact target so the cover slots into the platform's
        # actual rendering area instead of being a near-but-wrong
        # ratio. No-op when source already matches.
        final_image_bytes = _crop_image_to_aspect(
            final_image_bytes, data.aspect_ratio
        )

        sub_path = f"generated_images/article_cover/{creation_id}"
        storage_uri = await storage.save_file(
            final_image_bytes,
            file_name=f"{job.id}.png",
            sub_path=sub_path,
        )
        public_url = f"/uploads/{storage_uri}"

        job.image_url = public_url
        job.preview_url = public_url
        job.status = "completed"
        job.finished_at = _utcnow()

        creation.cover_image_url = public_url
        await db.commit()
    except AppError as e:
        job.status = "failed"
        job.error_message = f"{e.code}: {e.message}"
        job.finished_at = _utcnow()
        await db.commit()
        raise
    except Exception as e:
        logger.exception("Article cover generation failed for creation %s", creation_id)
        job.status = "failed"
        job.error_message = f"INTERNAL: {e}"
        job.finished_at = _utcnow()
        await db.commit()
        raise AppError("IMAGE_GENERATION_FAILED", str(e), 500) from e

    await db.refresh(job)
    return _to_response(job)


# ── Read / list / delete ────────────────────────────────────────────


async def get_image_job(
    db: AsyncSession, job_id: uuid.UUID
) -> ImageJobResponse:
    job = await db.get(ImageGenerationJob, job_id)
    if not job:
        raise NotFoundError("ImageGenerationJob", str(job_id))
    return _to_response(job)


async def list_image_jobs(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID | None = None,
    creation_id: uuid.UUID | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ImageJobResponse], int]:
    q = select(ImageGenerationJob)
    if offer_id:
        q = q.where(ImageGenerationJob.offer_id == offer_id)
    if creation_id:
        q = q.where(ImageGenerationJob.creation_id == creation_id)
    if status:
        q = q.where(ImageGenerationJob.status == status)

    from sqlalchemy import func

    total_q = select(func.count()).select_from(q.subquery())
    total = await db.scalar(total_q) or 0

    q = q.order_by(desc(ImageGenerationJob.created_at)).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return [_to_response(r) for r in rows], int(total)


async def delete_image_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    job = await db.get(ImageGenerationJob, job_id)
    if not job:
        raise NotFoundError("ImageGenerationJob", str(job_id))
    # Best-effort delete the on-disk image. Don't block on this — if
    # storage cleanup fails, the row should still go.
    if job.image_url and job.image_url.startswith("/uploads/"):
        try:
            storage_uri = job.image_url[len("/uploads/"):]
            await LocalStorageAdapter().delete_file(storage_uri)
        except Exception as e:
            logger.warning("Image file cleanup failed for job %s: %s", job.id, e)
    await db.delete(job)
    await db.commit()
