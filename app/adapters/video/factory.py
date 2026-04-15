"""Factory for instantiating video providers from a (name, credentials) pair.

Provider names and required credential keys:
    chanjing -> {"app_id": str, "secret_key": str}
    jogg     -> {"api_key": str}
    google   -> {"api_key": str}  # Gemini API key, also used for Veo
"""

from __future__ import annotations

from app.adapters.video.base import VideoProvider
from app.adapters.video.chanjing import ChanjingVideoProvider
from app.adapters.video.google_veo import GoogleVeoProvider
from app.adapters.video.jogg import JoggVideoProvider
from app.exceptions import AppError

SUPPORTED_PROVIDERS = ("chanjing", "jogg", "google")


def get_video_provider(provider: str, credentials: dict) -> VideoProvider:
    """Return a fresh VideoProvider instance for the given provider name."""
    if provider == "chanjing":
        return ChanjingVideoProvider(
            app_id=credentials.get("app_id", ""),
            secret_key=credentials.get("secret_key", ""),
        )
    if provider == "jogg":
        return JoggVideoProvider(api_key=credentials.get("api_key", ""))
    if provider == "google":
        return GoogleVeoProvider(api_key=credentials.get("api_key", ""))
    raise AppError(
        "UNKNOWN_PROVIDER",
        f"Unknown video provider: {provider!r}. Supported: {SUPPORTED_PROVIDERS}",
        400,
    )
