"""Smoke test: listing media providers must survive unknown provider names in DB.

The bug this guards against: pydantic `Literal` was used on the response schema's
`provider` field, so when the DB contained a provider string the code didn't
explicitly know about (e.g. `google` added later), `GET /api/v1/media-providers`
returned 500 on serialization.

Fix: Response uses `str`; Literal stays on Create/Validate (write boundaries).
"""
from __future__ import annotations

import uuid

from app.application.media_provider_service import _to_response
from app.models.media_provider_config import MediaProviderConfig
from app.schemas.media_provider import MediaProviderConfigResponse


class _FakeConfig:
    """Duck-typed stand-in for MediaProviderConfig (avoids DB dependency)."""

    def __init__(self, provider: str):
        self.id = uuid.uuid4()
        self.provider = provider
        self.label = f"Test {provider}"
        self.credentials = {"api_key": "secret-value-1234"}
        self.defaults = {}
        self.is_active = True


def test_response_serializes_unknown_provider():
    """Response schema must accept any provider string — DB is source of truth."""
    cfg = _FakeConfig("future_provider_xyz")
    resp = _to_response(cfg)  # type: ignore[arg-type]
    assert resp.provider == "future_provider_xyz"
    assert resp.is_active is True


def test_response_serializes_known_providers():
    """Regression: known providers still round-trip correctly."""
    for name in ("chanjing", "jogg", "google"):
        cfg = _FakeConfig(name)
        resp = _to_response(cfg)  # type: ignore[arg-type]
        assert resp.provider == name


def test_response_schema_is_permissive():
    """Direct schema check: Response must not enforce a Literal on provider."""
    # If this raises, someone re-added a Literal constraint to the response.
    resp = MediaProviderConfigResponse(
        id=str(uuid.uuid4()),
        provider="anything_goes",
        label="x",
        credentials={},
        defaults={},  # type: ignore[arg-type]
        is_active=True,
    )
    assert resp.provider == "anything_goes"
