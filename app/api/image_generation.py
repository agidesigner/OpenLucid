"""Image-generation API surface.

Two job-creation entry points:

  * POST /api/v1/image-jobs                  — poster mode (full template)
  * POST /api/v1/creations/{cid}/cover       — article cover (light)

Plus uniform read endpoints on /api/v1/image-jobs and a template list.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep, get_db
from app.exceptions import AppError
from app.application.image_service import (
    create_article_cover_job,
    create_brief_job,
    create_poster_job,
    create_refine_job,
    delete_image_job,
    derive_article_cover_suggestion,
    get_image_job,
    list_image_jobs,
    list_lineage,
    save_reference_upload,
    suggest_brief_references,
)
from app.application.image_template import list_templates
from app.schemas.common import PaginatedResponse
from app.schemas.image_generation import (
    ArticleCoverJobCreate,
    BriefJobCreate,
    CoverSuggestionResponse,
    ImageJobResponse,
    PosterJobCreate,
    ReferenceUploadResponse,
    ReferenceSuggestionsResponse,
    RefineJobCreate,
    TemplateOption,
    TemplateOptionsResponse,
    TemplateSlotSpec,
)


# ── Templates ───────────────────────────────────────────────────────


templates_router = APIRouter(prefix="/image-templates", tags=["image-generation"])


@templates_router.get("", response_model=TemplateOptionsResponse)
async def get_templates(lang: str = Query("zh-CN", description="UI language: zh-CN | en-US")):
    """Returns the static list of poster templates with their public slot specs."""
    is_en = (lang or "").lower().startswith("en")
    items = []
    for t in list_templates():
        slot_specs = [
            TemplateSlotSpec(
                key=s.key,
                label=s.label_en if is_en else s.label,
                input_type=s.input_type,
                max_chars=s.max_chars,
                required=s.required,
                placeholder=s.placeholder,
            )
            for s in t.slots
        ]
        items.append(
            TemplateOption(
                id=t.id,
                name=t.name_en if is_en else t.name_zh,
                description=t.description_en if is_en else t.description_zh,
                aspect_ratio=t.aspect_ratio,  # type: ignore[arg-type]
                slots=slot_specs,
            )
        )
    return TemplateOptionsResponse(templates=items)


# ── Jobs (poster + read/list/delete) ────────────────────────────────


jobs_router = APIRouter(prefix="/image-jobs", tags=["image-generation"])


@jobs_router.post("", response_model=ImageJobResponse, status_code=201)
async def create_poster(
    data: PosterJobCreate,
    db: AsyncSession = Depends(get_db),
):
    """Legacy slot-based poster generation. Prefer ``/brief`` for new clients."""
    return await create_poster_job(db, data)


@jobs_router.post("/brief", response_model=ImageJobResponse, status_code=201)
async def create_brief(
    data: BriefJobCreate,
    db: AsyncSession = Depends(get_db),
):
    """Brief-first generation — the primary path.

    User submits a free-form creative brief plus a curated reference
    set; the model receives the brief, references, brand logo, and
    optional QR / extras and composes the final image end-to-end.
    """
    return await create_brief_job(db, data)


_REFERENCE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024  # mirrors image_service constant
_REFERENCE_UPLOAD_CHUNK = 64 * 1024


@jobs_router.post(
    "/reference-upload",
    response_model=ReferenceUploadResponse,
    status_code=201,
)
async def upload_reference(
    file: UploadFile = File(...),
    offer_id: uuid.UUID = Form(...),
    role: str = Form("supplemental"),
):
    """Upload a one-off supplemental image for Image Studio.

    Unlike ``/assets/upload``, this does not create an Asset row and does
    not trigger tagging. It is scoped to a single generation request via
    the returned ``upload_id``.

    Body-size hardening (defense in depth):

      1. ``Content-Length`` / ``UploadFile.size`` fast-path — refuse
         oversize requests before reading a single byte.
      2. Chunked read with early abort — even when the size header lies
         (or is missing), we accumulate at most one chunk past the cap
         before raising. Memory ceiling is bounded.

      The HARD limit still belongs at the reverse proxy (nginx
      ``client_max_body_size``, Caddy ``request_body``, etc.) — this
      handler is the last line of defense, not the first.
    """
    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > _REFERENCE_UPLOAD_MAX_BYTES:
        raise AppError(
            "REFERENCE_UPLOAD_TOO_LARGE",
            f"Reference upload exceeds the {_REFERENCE_UPLOAD_MAX_BYTES // (1024 * 1024)}MB limit",
            413,
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_REFERENCE_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _REFERENCE_UPLOAD_MAX_BYTES:
            # Stop reading immediately; memory ceiling = MAX + one chunk.
            raise AppError(
                "REFERENCE_UPLOAD_TOO_LARGE",
                f"Reference upload exceeds the {_REFERENCE_UPLOAD_MAX_BYTES // (1024 * 1024)}MB limit",
                413,
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    return await save_reference_upload(
        file_content=content,
        file_name=file.filename or "reference",
        offer_id=offer_id,
        role=role,
    )


@jobs_router.post(
    "/suggest-references", response_model=ReferenceSuggestionsResponse
)
async def suggest_references(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Suggest reference assets for a brief — keyword-overlap-based.

    Body: ``{"offer_id": str, "brief": str, "limit": int = 3}``
    """
    import uuid as _uuid

    offer_raw = payload.get("offer_id") or ""
    brief = payload.get("brief") or ""
    try:
        limit = int(payload.get("limit") or 3)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(limit, 6))
    try:
        offer_id = _uuid.UUID(offer_raw)
    except (ValueError, TypeError):
        return ReferenceSuggestionsResponse(suggestions=[])

    suggestions = await suggest_brief_references(
        db, offer_id=offer_id, brief=brief, limit=limit
    )
    return ReferenceSuggestionsResponse(suggestions=suggestions)


@jobs_router.get("", response_model=PaginatedResponse[ImageJobResponse])
async def list_jobs(
    pagination: PaginationDep,
    offer_id: uuid.UUID | None = Query(None),
    creation_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    items, total = await list_image_jobs(
        db,
        offer_id=offer_id,
        creation_id=creation_id,
        status=status,
        **pagination,
    )
    return PaginatedResponse(items=items, total=total, **pagination)


@jobs_router.get("/{job_id}", response_model=ImageJobResponse)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await get_image_job(db, job_id)


@jobs_router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await delete_image_job(db, job_id)


@jobs_router.post(
    "/{job_id}/refine", response_model=ImageJobResponse, status_code=201
)
async def refine_job(
    job_id: uuid.UUID,
    data: RefineJobCreate,
    db: AsyncSession = Depends(get_db),
):
    """Iterative single-image refinement.

    Takes the parent image bytes + a short instruction and produces a new
    version on the same lineage. Requires an /v1/images/edits-capable
    provider — without image input there's no way to refine.
    """
    return await create_refine_job(db, job_id, data)


@jobs_router.get("/{job_id}/lineage", response_model=list[ImageJobResponse])
async def get_lineage(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Full version history (v1 → v2 → ...) for the lineage containing this job."""
    return await list_lineage(db, job_id)


# ── Article cover (creation-scoped) ─────────────────────────────────


creations_cover_router = APIRouter(
    prefix="/creations/{creation_id}/cover",
    tags=["image-generation"],
)


@creations_cover_router.post("", response_model=ImageJobResponse, status_code=201)
async def generate_article_cover(
    creation_id: uuid.UUID,
    data: ArticleCoverJobCreate,
    db: AsyncSession = Depends(get_db),
):
    """Generate a cover image for an article-style creation.

    Writes the resulting URL back to ``creations.cover_image_url`` on
    success — content-studio reads that field directly.
    """
    return await create_article_cover_job(db, creation_id, data)


@creations_cover_router.get("/suggest", response_model=CoverSuggestionResponse)
async def suggest_article_cover(
    creation_id: uuid.UUID,
    platform_id: str | None = Query(None, max_length=80),
    db: AsyncSession = Depends(get_db),
):
    """LLM-derived cover hints for the cover panel.

    Single round-trip returns brief + visual tags + tag-overlap asset
    suggestions + platform-derived aspect. The panel pre-fills the form
    with these so users can hit "generate" without typing.

    Brief and tags follow the article's language (so tag overlap against
    the user's asset-library tags actually matches). Aspect is decided
    by ``platform_id`` — the caller is the only side that knows which
    platform the article was authored for.
    """
    return await derive_article_cover_suggestion(db, creation_id, platform_id)
