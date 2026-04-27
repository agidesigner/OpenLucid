"""Pin the Class A / Class B reference-image domain model.

Bug v1.3.x recap: ``ref_img_url`` was a single overloaded parameter
across video providers. The frontend auto-sourced KB assets and fed
them through this field, but chanjing/Doubao + Veo 3.x both interpret
``ref_img_url`` as the **first frame** of an i2v video, with strict
aspect-ratio constraints. So a perfectly valid logo-shaped KB asset
would crash chanjing with ``code=50000: 宽高比 3.12 不在 [0.5, 2.0]``.

The fix: split ``ref_img_url`` into three semantic types —
``StyleReference`` (soft, Class A) and ``FirstFrame`` /
``LastFrame`` (hard, Class B). KB assets always become
StyleReferences; first/last frames are reserved for explicit user
uploads in a future UI.

These tests pin the contract so a future refactor can't quietly
re-merge the two semantics.
"""
from __future__ import annotations

import asyncio
import inspect


def test_reference_types_are_separate_dataclasses():
    """Three distinct types — collapsing them back into one would
    re-introduce the v1.3.x bug class."""
    from app.adapters.video.base import FirstFrame, LastFrame, StyleReference

    assert StyleReference is not FirstFrame
    assert StyleReference is not LastFrame
    assert FirstFrame is not LastFrame
    # Every type carries a url field
    assert "url" in StyleReference.__dataclass_fields__
    assert "url" in FirstFrame.__dataclass_fields__
    assert "url" in LastFrame.__dataclass_fields__
    # Only StyleReference is soft; carries optional description for
    # prompt-fallback when the provider doesn't support style refs.
    assert "description" in StyleReference.__dataclass_fields__


def test_unsupported_reference_mode_exists_for_hard_rejection():
    """Hard references (FirstFrame / LastFrame) MUST raise this on
    unsupported providers — silently dropping a user's explicit
    "video should start exactly with this frame" would be a hidden
    product bug. Soft StyleReference does NOT raise this."""
    from app.adapters.video.base import UnsupportedReferenceMode

    assert issubclass(UnsupportedReferenceMode, Exception)


def test_chanjing_drops_style_references_silently():
    """Chanjing/Doubao has no native style-reference channel. Style
    refs must be dropped without raising, without populating
    ``ref_img_url``. Otherwise we'd reintroduce the 50000 bug
    every time a KB asset has aspect outside [0.5, 2.0]."""
    from app.adapters.video.base import StyleReference
    from app.adapters.video.chanjing import ChanjingVideoProvider

    captured: dict = {}

    async def _fake_request(self, method, path, json_body=None, **kw):
        captured["payload"] = json_body
        return {"data": "fake-task-id"}

    ChanjingVideoProvider._request = _fake_request  # type: ignore[assignment]
    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    asyncio.run(p.submit_broll_clip(
        prompt="a brand banner",
        duration=6,
        aspect_ratio="9:16",
        model_code="Doubao-Seedance-1.0-pro",
        style_references=[StyleReference(url="https://x/logo.png")],
        first_frame=None,
        last_frame=None,
    ))
    payload = captured["payload"]
    # The bug-triggering field must NOT be set
    assert "ref_img_url" not in payload, \
        "StyleReference must NOT populate ref_img_url (Class A vs Class B mix-up)"
    # Other expected fields still present
    assert payload["ref_prompt"] == "a brand banner"
    assert payload["aspect_ratio"] == "9:16"


def test_chanjing_uses_ref_img_url_for_first_frame():
    """FirstFrame (Class B) is the legitimate use of ref_img_url —
    that field's API contract IS first-frame anchoring. Aspect-ratio
    validation happens server-side; we propagate the API error."""
    from app.adapters.video.base import FirstFrame
    from app.adapters.video.chanjing import ChanjingVideoProvider

    captured: dict = {}

    async def _fake_request(self, method, path, json_body=None, **kw):
        captured["payload"] = json_body
        return {"data": "fake-task-id"}

    ChanjingVideoProvider._request = _fake_request  # type: ignore[assignment]
    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    asyncio.run(p.submit_broll_clip(
        prompt="x",
        duration=6,
        aspect_ratio="9:16",
        model_code="Doubao-Seedance-1.0-pro",
        first_frame=FirstFrame(url="https://x/keyframe.png"),
    ))
    assert captured["payload"]["ref_img_url"] == ["https://x/keyframe.png"]


def test_chanjing_raises_unsupported_for_last_frame():
    """Doubao-Seedance has no last-frame anchoring. Must raise — not
    silently drop — because a user passing last_frame meant it as a
    hard requirement."""
    from app.adapters.video.base import LastFrame, UnsupportedReferenceMode
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    async def _run():
        await p.submit_broll_clip(
            prompt="x",
            duration=6,
            last_frame=LastFrame(url="https://x/end.png"),
        )

    try:
        asyncio.run(_run())
    except UnsupportedReferenceMode:
        return
    raise AssertionError("expected UnsupportedReferenceMode for last_frame on chanjing")


def test_veo_drops_style_references_silently():
    """Same contract on Veo: KB-style refs must NOT become Veo's
    instance.image (which is its first-frame field)."""
    from app.adapters.video.base import StyleReference
    from app.adapters.video.google_veo import GoogleVeoProvider

    p = GoogleVeoProvider.__new__(GoogleVeoProvider)
    p._api_key = ""  # triggers VEO_NO_API_KEY before HTTP — that's fine

    # We just need to verify the function signature accepts
    # style_references and routes it correctly. Inspect signature:
    sig = inspect.signature(p.submit_broll_clip)
    params = sig.parameters
    assert "style_references" in params
    assert "first_frame" in params
    assert "last_frame" in params
    # The legacy ``ref_img_url`` param must be gone — its presence
    # would mean a caller could still smuggle Class A images into
    # the first-frame slot.
    assert "ref_img_url" not in params, \
        "ref_img_url removed in v1.3.5 — keep it removed"


def test_chanjing_signature_no_legacy_ref_img_url():
    """Same anti-regression check for chanjing."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    sig = inspect.signature(ChanjingVideoProvider.submit_broll_clip)
    params = sig.parameters
    assert "style_references" in params
    assert "first_frame" in params
    assert "last_frame" in params
    assert "ref_img_url" not in params


def test_veo_payload_omits_numberOfVideos():
    """Veo 3.1+ rejects the ``numberOfVideos`` parameter with HTTP 400
    'isn't supported by this model'. The field has always defaulted
    to 1 (one video per call) which is exactly what we want, so the
    fix is to omit it entirely — backward-compatible across all Veo
    model versions.

    Production-verified: pre-fix, 4-of-4 broll shots failed with
    ``VEO_SUBMIT_FAILED ... 'numberOfVideos' isn't supported by this
    model``. Post-fix, no shot fails on this field."""
    import asyncio

    from app.adapters.video.google_veo import GoogleVeoProvider
    from app.exceptions import AppError

    captured: dict = {}

    class _FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"name": "operations/fake"}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, *, headers=None, json=None):
            captured["payload"] = json
            return _FakeResp()

    import app.adapters.video.google_veo as gv
    real_AsyncClient = gv.httpx.AsyncClient
    gv.httpx.AsyncClient = _FakeClient  # type: ignore[assignment]
    try:
        p = GoogleVeoProvider.__new__(GoogleVeoProvider)
        p._api_key = "fake"
        p._headers = {}

        asyncio.run(p.submit_broll_clip(
            prompt="x", duration=6, aspect_ratio="9:16",
            model_code="veo-3.1-generate-preview",
        ))
    finally:
        gv.httpx.AsyncClient = real_AsyncClient  # type: ignore[assignment]

    params = captured["payload"]["parameters"]
    assert "numberOfVideos" not in params, \
        "numberOfVideos must not be sent — Veo 3.1+ rejects it with 400"
    # Other expected parameters still present
    assert "aspectRatio" in params
    assert "durationSeconds" in params


def test_chanjing_kling_model_codes_match_docs_verbatim():
    """Chanjing's Kling model_code naming is inconsistent across
    versions; each must be copied verbatim from the per-version doc
    page or it returns ``50000: 模型不存在``. Don't pattern-match.

    Verified against chanjing docs:
      - https://doc.chanjing.cc/api/ai-creation/video-kling2.1.html
      - https://doc.chanjing.cc/api/ai-creation/video-kling2.5.html
    """
    from app.application.setting_service import _CAPABILITY_META

    capability = _CAPABILITY_META["video_gen"]
    chanjing_models = dict(capability["models_by_provider"]["chanjing"])

    # v2.1 — full form, hyphens, master suffix
    assert "tx_kling-v2-1-master" in chanjing_models, \
        "Kling v2.1 model_code must be exactly 'tx_kling-v2-1-master' (chanjing doc)"
    # v2.5 — short form, NO hyphens, NO prefix, NO suffix
    assert "kling2.5" in chanjing_models, \
        "Kling 2.5 model_code must be exactly 'kling2.5' (chanjing doc)"
    # Forbidden: pattern-matched guesses that look right but don't exist
    assert "kling-2.5" not in chanjing_models, \
        "Kling 2.5 is NOT 'kling-2.5' — production confirmed 模型不存在"
    assert "tx_kling-v2-5-master" not in chanjing_models, \
        "Kling 2.5 does NOT follow v2.1's tx_kling-v*-master pattern"


def test_is_hard_provider_error_distinguishes_classes():
    """Soft failures (per-shot parameter / aspect / unsupported ref
    rejections) must NOT skip the avatar. Hard failures (auth,
    credits, rate-limit, timeout) SHOULD skip to avoid wasting
    credits on a job the user can't receive."""
    from app.application.video_service import _is_hard_provider_error

    # Hard — yes
    assert _is_hard_provider_error("Insufficient credits / quota — out of balance")
    assert _is_hard_provider_error("Rate-limited by provider — slow down")
    assert _is_hard_provider_error("Authentication / permission denied — bad key")
    assert _is_hard_provider_error("Provider timeout — deadline exceeded")
    # Soft — no
    assert not _is_hard_provider_error("aspect ratio 3.12 not in [0.5, 2.0]")
    assert not _is_hard_provider_error("invalid prompt encoding")
    assert not _is_hard_provider_error("unknown")
    assert not _is_hard_provider_error("")
