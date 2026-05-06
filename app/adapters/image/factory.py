"""Factory for instantiating image providers from a (name, credentials, model) triple.

Mirrors ``app.adapters.video.factory.get_video_provider`` so the
service layer doesn't have to know the per-provider constructor shape.
The Protocol is in ``app.adapters.image.base.ImageProvider``.
"""

from __future__ import annotations

from app.adapters.image.base import ImageProvider
from app.adapters.image.chanjing_image import ChanjingImageProvider
from app.adapters.image.google_image import GoogleImageProvider
from app.adapters.image.gpt_image import GPTImageProvider
from app.exceptions import AppError

# Provider names accepted on the image side. ``openai_image`` is the
# canonical media_provider_configs.provider value; ``openai`` is also
# accepted because the synthetic LLMConfig-backed virtual provider
# (see setting_service.get_media_capability_configs) exposes the same
# OpenAI key as a virtual MPC under provider='openai'.
SUPPORTED_PROVIDERS = ("openai", "openai_image", "chanjing", "google")


def get_image_provider(
    provider: str,
    credentials: dict,
    *,
    model: str | None = None,
) -> ImageProvider:
    """Return a fresh ImageProvider instance for the given provider name."""
    if provider in ("openai", "openai_image"):
        return GPTImageProvider(
            api_key=credentials.get("api_key", ""),
            base_url=credentials.get("base_url") or None,
            model=model or credentials.get("model") or "gpt-image-1",
        )
    if provider == "chanjing":
        return ChanjingImageProvider(
            app_id=credentials.get("app_id", ""),
            secret_key=credentials.get("secret_key", ""),
            model=model or "doubao-seedream-4.5",
        )
    if provider == "google":
        return GoogleImageProvider(
            api_key=credentials.get("api_key", ""),
            model=model or "gemini-3-pro-image-preview",
        )
    raise AppError(
        "UNKNOWN_IMAGE_PROVIDER",
        f"Unknown image provider: {provider!r}. Supported: {SUPPORTED_PROVIDERS}",
        400,
    )
