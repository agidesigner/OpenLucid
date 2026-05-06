from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal["pending", "processing", "completed", "failed"]
ImageMode = Literal["poster", "article_cover"]
PosterAspect = Literal["9:16", "1:1", "3:4", "4:5"]
# Article cover aspect — wider than poster because covers serve every
# platform we publish to (公众号 long horizontal, 小红书 vertical,
# LinkedIn / Substack OG cards 1.91:1, X / blog 16:9, IG portrait 4:5).
ArticleAspect = Literal[
    "9:16", "1:1", "16:9", "4:3", "3:4", "4:5", "1.91:1", "2.35:1"
]


class TemplateSlotInput(BaseModel):
    """A user-filled slot value for a poster template.

    The template definition tells the renderer where each slot lands and
    how it's typeset; this schema only carries the user's text/asset for
    each slot key.
    """

    key: str
    value: str  # text content; for image slots this is a storage URI / URL


class PosterJobCreate(BaseModel):
    """Legacy: POST /api/v1/image-jobs body for the slot-based template flow.

    Kept for backward compatibility with old clients; new clients should
    use BriefJobCreate (POST /api/v1/image-jobs/brief), which abstracts
    the input to a free-form intent and lets the model + reference images
    do the composition.
    """

    offer_id: str
    template_id: str
    selling_point: str = Field(..., min_length=1, max_length=512)
    slot_values: dict[str, str] = Field(default_factory=dict)
    aspect_ratio: PosterAspect = "9:16"
    qr_url: str | None = None  # encoded into a QR code at render time
    qr_asset_uri: str | None = None  # alternative: user-provided QR image (storage URI)


# Aspect chips the brief-first UI exposes — wider set than poster-only,
# since the same flow can produce article covers (16:9 / 4:3 / 1:1).
BriefAspect = Literal["9:16", "1:1", "16:9", "4:3", "3:4", "4:5"]
ReferenceUploadRole = Literal["supplemental", "qr"]


class ReferenceUploadInput(BaseModel):
    """One-off reference image uploaded for a single image-generation job.

    These uploads are intentionally not Asset rows: they do not enter the
    reusable asset library, do not trigger tagging, and do not participate
    in future auto-recommendations unless the user explicitly saves them
    to the library through the normal asset-upload flow.
    """

    upload_id: str = Field(..., min_length=1, max_length=1024)
    role: ReferenceUploadRole = "supplemental"
    label: str | None = Field(None, max_length=255)
    # ``url`` is accepted for backward-compat with the original frontend
    # that round-tripped the response of /reference-upload. The SERVER
    # IGNORES IT — the audit trail's url is always derived from the
    # validated upload_id so a direct API caller can't inject an
    # external URL into the "AI used these materials" panel.
    url: str | None = Field(None, max_length=1024)


class BriefJobCreate(BaseModel):
    """POST /api/v1/image-jobs/brief — the brief-first image-generation flow.

    Replaces the slot/template path with a single creative brief plus a
    curated reference set. The model receives:
      - The brief text (full intent)
      - Offer KB context (auto-resolved from offer_id)
      - Brand logo + voice (auto-resolved from brandkit)
      - Reference images (user-curated; auto-suggested when empty)
      - Optional QR + extra assets

    The model decides composition, layout, palette, and typography. This
    is the trust-the-model path the slot system was over-engineered around.
    """

    offer_id: str
    brief: str = Field(..., min_length=1, max_length=500)
    aspect_ratio: BriefAspect = "9:16"
    # Asset IDs the user explicitly picked as style references. When empty
    # the server auto-suggests using brief-keyword × tag overlap, falling
    # back to top-N by hook_score. Cap at 5 to bound the multipart upload
    # size and provider rate-limit exposure — the frontend already
    # enforces this, but a direct API caller could otherwise pass any
    # number of large image bytes through to the model.
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=5)
    # Additional images (product shots / mascots / etc.) — passed alongside
    # the style references. Kept separate so the prompt can describe them
    # differently to the model if needed.
    extra_asset_ids: list[str] = Field(default_factory=list, max_length=4)
    # One-off uploads for this job only. They are not saved as assets and
    # therefore do not get tagged or recommended later.
    extra_uploads: list[ReferenceUploadInput] = Field(default_factory=list, max_length=4)
    # Preferred: asset_id of the QR upload — the server resolves it against
    # the asset table and verifies offer-scope membership before reading
    # the file. Path-traversal-safe by construction.
    qr_asset_id: str | None = None
    # Legacy: raw storage URI. Still accepted for backward compat but
    # subject to a stricter normpath check (rejects '..' segments).
    # New clients should send qr_asset_id instead.
    qr_asset_uri: str | None = None
    # Optional per-job model override. When unset, the service uses the
    # MediaCapabilityDefault('image_gen') row (set in
    # /settings/media-capabilities) and falls back to the legacy OpenAI
    # LLMConfig. Both fields are required as a pair: model_code alone
    # isn't enough to locate the credential row (e.g. two providers
    # could both expose gpt-image-2). Mirrors the broll picker contract
    # in VideoGenerateRequest.
    image_provider_config_id: str | None = None
    image_model_code: str | None = None


class ReferenceSuggestion(BaseModel):
    asset_id: str
    score: float
    reason: str  # short text — "matches selling-point X", "high reuse_score", etc.


class ReferenceSuggestionsResponse(BaseModel):
    suggestions: list[ReferenceSuggestion]


class ReferenceUploadResponse(BaseModel):
    upload_id: str
    url: str
    role: ReferenceUploadRole
    label: str | None = None


class RefineJobCreate(BaseModel):
    """POST /api/v1/image-jobs/{job_id}/refine — iterative single-image edit.

    Takes a short refinement instruction ("logo 放大", "背景换冷色")
    and produces a new image conditioned on the parent image bytes
    plus the refinement text. Original brief, offer, brandkit context
    flow through automatically — the user shouldn't have to repeat them.
    """

    refinement: str = Field(..., min_length=1, max_length=200)


class ArticleCoverJobCreate(BaseModel):
    """POST /api/v1/creations/{cid}/cover body.

    Two paths share this schema:
      - Light path (legacy): only ``aspect_ratio`` + optional
        ``extra_prompt``. The server auto-builds a prompt from article
        title + body + offer hints and calls the model with NO reference
        images. Cheap, lower fidelity. Existing clients keep working.
      - Brief-first path (new): caller passes ``brief`` plus a curated
        reference set (``reference_asset_ids`` / ``extra_asset_ids`` /
        ``extra_uploads``). The server feeds reference images to the
        model so the cover stays visually consistent with the offer's
        brand kit and the article's tone. Used by the content-studio
        cover panel.

    Refining an existing cover (v1 → v2) goes through the regular
    ``POST /api/v1/image-jobs/{job_id}/refine`` endpoint — that path
    already inherits ``mode`` + ``creation_id`` from the parent job, so
    we don't duplicate the lineage logic here.
    """

    aspect_ratio: ArticleAspect = "16:9"
    # Free-form image brief. When set, the brief-first path runs
    # (with reference images fed to the model). When None / empty, the
    # legacy auto-prompt path is preserved for backward compat.
    brief: str | None = Field(None, max_length=500)
    extra_prompt: str | None = Field(None, max_length=512)
    # Style references picked by the user (or auto-suggested by
    # /cover-suggest). Caps mirror BriefJobCreate so the multipart
    # upload to the image provider stays bounded.
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=5)
    extra_asset_ids: list[str] = Field(default_factory=list, max_length=4)
    extra_uploads: list[ReferenceUploadInput] = Field(default_factory=list, max_length=4)
    # Per-job model override (mirrors BriefJobCreate). When unset, the
    # service uses the MediaCapabilityDefault('image_gen') row and
    # falls back to the OpenAI LLMConfig.
    image_provider_config_id: str | None = None
    image_model_code: str | None = None


class CoverSuggestionResponse(BaseModel):
    """GET /api/v1/creations/{cid}/cover-suggest result.

    A single LLM call derives both the brief and the visual tags. We
    return both even though tags only feed asset lookup — the panel
    shows them as informational chips so the user can sanity-check the
    model's read of the article before generating.

    Asset suggestions are computed server-side via tag overlap against
    the offer's asset library. Aspect is derived from the platform id
    the panel passes in (defaults to 16:9 when platform unknown).
    """

    brief: str
    tags: list[str]
    suggested_asset_ids: list[str]
    aspect_ratio: ArticleAspect


class ImageJobResponse(BaseModel):
    id: str
    mode: ImageMode
    creation_id: str | None
    offer_id: str | None
    brandkit_id: str | None
    template_id: str | None
    provider: str
    provider_config_id: str | None
    status: JobStatus
    params: dict
    image_url: str | None
    preview_url: str | None
    progress: int | None
    error_message: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str
    updated_at: str


class TemplateSlotSpec(BaseModel):
    """Public spec of one template slot — what the UI needs to render
    the input form for it. Renderer-internal layout details (pixel
    positions, fonts) are NOT exposed."""

    key: str
    label: str
    input_type: Literal["text", "qr", "image"]
    max_chars: int | None = None
    required: bool = True
    placeholder: str | None = None


class TemplateOption(BaseModel):
    """Returned by GET /api/v1/image-templates."""

    id: str
    name: str
    description: str
    aspect_ratio: PosterAspect
    preview_url: str | None = None  # static preview thumbnail (optional)
    slots: list[TemplateSlotSpec]


class TemplateOptionsResponse(BaseModel):
    templates: list[TemplateOption]
