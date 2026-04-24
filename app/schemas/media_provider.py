from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["chanjing", "jogg", "google"]
AspectRatio = Literal["portrait", "landscape", "square"]


# ── Defaults ────────────────────────────────────────────────────────


class MediaProviderDefaults(BaseModel):
    """User-selected defaults for video generation. All optional."""

    avatar_id: str | None = None
    voice_id: str | None = None
    aspect_ratio: AspectRatio | None = "portrait"


# ── CRUD payloads ───────────────────────────────────────────────────


class MediaProviderConfigCreate(BaseModel):
    provider: ProviderName
    label: str = Field(..., min_length=1, max_length=255)
    # Provider-specific keys: chanjing → {app_id, secret_key}; jogg → {api_key}
    credentials: dict[str, str]
    defaults: MediaProviderDefaults = MediaProviderDefaults()


class MediaProviderConfigUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=255)
    credentials: dict[str, str] | None = None
    defaults: MediaProviderDefaults | None = None


class MediaProviderConfigResponse(BaseModel):
    """Response shape — credentials values are masked.

    `provider` is `str` not `Literal` on purpose: the DB is the source of truth
    and may contain legacy/future provider names the current code doesn't know
    about. Reads must be permissive. Writes (Create/Validate) still use the
    Literal — that's where we enforce the enum.
    """

    id: str
    provider: str
    label: str
    # Plaintext credentials returned so the edit modal can pre-fill
    # without a second round-trip. Same rationale as LLMConfigResponse.
    credentials: dict[str, str]
    defaults: MediaProviderDefaults
    is_active: bool

    model_config = {"from_attributes": True}


class MediaProviderValidateRequest(BaseModel):
    """Validate raw credentials before saving (no DB write)."""

    provider: ProviderName
    credentials: dict[str, str]


# ── Avatar / Voice listing (proxied to provider) ────────────────────


class AvatarItem(BaseModel):
    id: str
    name: str
    gender: str | None = None  # normalized: "male" | "female" | None
    age: str | None = None      # normalized: "young" | "adult" | "senior" | None
    preview_image_url: str
    preview_video_url: str | None = None
    # Provider-specific hints (e.g. Chanjing figure_type, paired_voice_id) that
    # the frontend should echo back via VideoGenerateRequest.provider_extras at
    # create time.
    extras: dict = {}


class VoiceItem(BaseModel):
    id: str
    name: str
    gender: str | None = None  # normalized
    age: str | None = None      # normalized
    language: str | None = None
    sample_url: str


class VoicePreviewRequest(BaseModel):
    """POST /api/v1/media-providers/{id}/voices/{voice_id}/preview body."""

    text: str = Field(..., min_length=1, max_length=4000)


class VoicePreviewResponse(BaseModel):
    audio_url: str
