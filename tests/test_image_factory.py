"""Pin the image-provider factory dispatch contract.

The factory is the single entry point image_service uses to build a
provider instance from a media_provider_configs row. A future refactor
that breaks the (provider, credentials) → adapter mapping would silently
route the wrong adapter — these assertions catch that.
"""
from __future__ import annotations

import pytest


def test_factory_routes_openai_to_gpt_adapter():
    from app.adapters.image.factory import get_image_provider
    from app.adapters.image.gpt_image import GPTImageProvider

    p = get_image_provider("openai", {"api_key": "sk-test"}, model="gpt-image-2")
    assert isinstance(p, GPTImageProvider)
    assert p.model == "gpt-image-2"


def test_factory_routes_openai_image_alias_to_gpt_adapter():
    """``openai_image`` is the canonical media_provider_configs.provider
    value (used by users who set a dedicated image-only credential row)
    while ``openai`` is the synthetic LLMConfig-backed path. Both must
    land on the same adapter."""
    from app.adapters.image.factory import get_image_provider
    from app.adapters.image.gpt_image import GPTImageProvider

    p = get_image_provider(
        "openai_image",
        {"api_key": "sk-test", "base_url": "https://api.openai.com/v1"},
        model="gpt-image-1",
    )
    assert isinstance(p, GPTImageProvider)
    assert p.model == "gpt-image-1"


def test_factory_routes_chanjing_to_chanjing_image_adapter():
    from app.adapters.image.chanjing_image import ChanjingImageProvider
    from app.adapters.image.factory import get_image_provider

    p = get_image_provider(
        "chanjing",
        {"app_id": "id", "secret_key": "sec"},
        model="doubao-seedream-4.5",
    )
    assert isinstance(p, ChanjingImageProvider)
    assert p.model == "doubao-seedream-4.5"


def test_factory_routes_google_to_google_image_adapter():
    from app.adapters.image.factory import get_image_provider
    from app.adapters.image.google_image import GoogleImageProvider

    p = get_image_provider(
        "google",
        {"api_key": "fake-key"},
        model="gemini-3-pro-image-preview",
    )
    assert isinstance(p, GoogleImageProvider)
    assert p.model == "gemini-3-pro-image-preview"


def test_factory_rejects_unknown_provider():
    from app.adapters.image.factory import get_image_provider
    from app.exceptions import AppError

    with pytest.raises(AppError) as exc_info:
        get_image_provider("midjourney", {"api_key": "x"})
    assert exc_info.value.code == "UNKNOWN_IMAGE_PROVIDER"


def test_factory_falls_back_to_provider_default_model():
    """When no model is passed and credentials don't carry one, the
    factory uses each provider's documented default — keeps callers
    that don't bother passing ``model`` from breaking."""
    from app.adapters.image.factory import get_image_provider
    from app.adapters.image.chanjing_image import ChanjingImageProvider
    from app.adapters.image.google_image import GoogleImageProvider

    p_chanjing = get_image_provider("chanjing", {"app_id": "i", "secret_key": "s"})
    assert isinstance(p_chanjing, ChanjingImageProvider)
    assert p_chanjing.model == "doubao-seedream-4.5"

    p_google = get_image_provider("google", {"api_key": "k"})
    assert isinstance(p_google, GoogleImageProvider)
    assert p_google.model == "gemini-3-pro-image-preview"
