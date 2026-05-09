"""Pin the gpt_image error-humanizer contract.

Production incident: a user's OpenAI-compatible proxy didn't have the
default `gpt-image-2` model installed. The upstream returned
``Error code: 503 - {'error': {'code': 'model_not_found', 'message':
'模型 gpt-image-2 不存在'}}``. The frontend showed that JSON blob
verbatim — readable to engineers, opaque to the user.

The humanizer fixes this by:
  1. Detecting the ``model_not_found`` pattern in three flavors
     (canonical English code, OpenAI prose, Chinese-localized proxy).
  2. Probing the proxy's ``/v1/models`` to learn what IS available.
  3. Returning a single sentence the user can act on:
     "你配置的代理里没有模型 X. 代理实际支持的图像模型: A, B. 请去
     设置 → 媒体能力 切换 model."

These tests pin the contract so a future "simplification" doesn't
quietly drop the helpful path and bring back the JSON-blob UX.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_provider(available_models: list[str] | None = None):
    """Construct a GPTImageProvider with the openai client mocked.

    When ``available_models`` is None, the models.list() probe raises —
    matching a misbehaving proxy that 401/500s on /v1/models.
    """
    from app.adapters.image.gpt_image import GPTImageProvider

    p = GPTImageProvider(api_key="sk-test", model="gpt-image-2")
    if available_models is None:
        p.client.models.list = AsyncMock(side_effect=RuntimeError("probe failed"))
    else:
        page = MagicMock()
        page.data = [MagicMock(id=name) for name in available_models]
        p.client.models.list = AsyncMock(return_value=page)
    return p


# ── Detection ─────────────────────────────────────────────────


def test_detects_canonical_model_not_found_code():
    from app.adapters.image.gpt_image import GPTImageProvider

    raw = "Error code: 503 - {'error': {'code': 'model_not_found', 'message': 'oops'}}"
    assert GPTImageProvider._looks_like_model_not_found(raw) is True


def test_detects_openai_prose_does_not_exist():
    from app.adapters.image.gpt_image import GPTImageProvider

    raw = "The model gpt-image-2 does not exist or you do not have access to it."
    assert GPTImageProvider._looks_like_model_not_found(raw) is True


def test_detects_chinese_localized_proxy_message():
    """The user's proxy returns: ``模型 gpt-image-2 不存在，请检查参数``."""
    from app.adapters.image.gpt_image import GPTImageProvider

    raw = "{'error': {'code': '', 'message': '模型 gpt-image-2 不存在，请检查参数'}}"
    assert GPTImageProvider._looks_like_model_not_found(raw) is True


def test_does_not_misfire_on_unrelated_errors():
    from app.adapters.image.gpt_image import GPTImageProvider

    for benign in [
        "Connection error.",
        "Request timed out.",
        "401 Unauthorized: invalid api_key",
        "Rate limit reached for requests",
        "Content policy violation",
        "",
    ]:
        assert GPTImageProvider._looks_like_model_not_found(benign) is False, benign


# ── Humanizer output ──────────────────────────────────────────


def test_humanizer_lists_actually_available_models():
    p = _make_provider(available_models=[
        "claude-sonnet-4-6",
        "gpt-image-1.5",
        "deepseek-chat",
        "dall-e-3",
    ])
    raw = "{'error': {'code': 'model_not_found', 'message': '模型 gpt-image-2 不存在'}}"
    msg = _run(p._humanize_image_error(raw))

    assert "gpt-image-2" in msg
    assert "gpt-image-1.5" in msg
    assert "dall-e-3" in msg
    assert "claude-sonnet-4-6" not in msg
    assert "deepseek-chat" not in msg
    assert "设置" in msg


def test_humanizer_handles_proxy_with_no_image_models():
    p = _make_provider(available_models=[
        "claude-sonnet-4-6",
        "deepseek-chat",
        "gpt-5",
    ])
    raw = "{'error': {'code': 'model_not_found', 'message': '模型 gpt-image-2 不存在'}}"
    msg = _run(p._humanize_image_error(raw))

    assert "gpt-image-2" in msg
    assert "未列出任何图像模型" in msg
    assert "蝉镜" in msg


def test_humanizer_handles_failed_probe():
    p = _make_provider(available_models=None)
    raw = "{'error': {'code': 'model_not_found', 'message': '模型 X 不存在'}}"
    msg = _run(p._humanize_image_error(raw))

    assert "gpt-image-2" in msg
    assert "未列出任何图像模型" in msg or "确认 image API" in msg


def test_humanizer_passes_through_non_model_errors():
    p = _make_provider(available_models=["gpt-image-1.5"])

    msg = _run(p._humanize_image_error("Connection error."))
    assert "Connection error" in msg
    assert "gpt-image-1.5" not in msg

    msg = _run(p._humanize_image_error("401 Unauthorized: invalid api_key"))
    assert "401" in msg or "Unauthorized" in msg or "api_key" in msg


def test_proxy_models_cache_avoids_redundant_probes():
    p = _make_provider(available_models=["gpt-image-1.5"])
    _run(p._list_proxy_image_models())
    _run(p._list_proxy_image_models())
    _run(p._list_proxy_image_models())
    assert p.client.models.list.await_count == 1


# ── /v1/images/edits 400 disambiguation ───────────────────────
#
# The reference-image path (generate_with_references) calls
# /v1/images/edits and historically treated any 400 with a
# model-not-found body as "this proxy doesn't expose edits" →
# UnsupportedReferenceMode (caller falls back to /v1/images/generations).
# That conflates two real cases:
#   (A) The user picked a model that isn't on the proxy at all —
#       the right answer is to humanize and surface "switch model".
#   (B) The model IS on the proxy, but /v1/images/edits can't serve
#       it (older proxies, plain OpenAI free tier without edits, etc.)
#       — the right answer is the legacy fallback.
# The disambiguation uses the cached /v1/models probe.


def _make_edits_400_response(body: str):
    """A faux httpx response for the 400 branch."""
    resp = MagicMock()
    resp.status_code = 400
    resp.text = body
    return resp


def _call_generate_with_references_for_400(provider, body: str):
    """Drive the real generate_with_references 400 branch."""
    from app.adapters.image.base import GenerateWithReferencesRequest

    fake_post = AsyncMock(return_value=_make_edits_400_response(body))

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            self.post = fake_post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    req = GenerateWithReferencesRequest(
        prompt="test prompt",
        references=[b"FAKE_PNG_BYTES"],
        aspect_ratio="9:16",
    )
    with patch("httpx.AsyncClient", _FakeClient):
        return _run(provider.generate_with_references(req))


def test_edits_400_with_chinese_body_and_model_not_in_proxy_humanizes():
    """Reproduces the production case: proxy returns 400 with Chinese
    'model_not_found', the model genuinely isn't on the proxy. The
    400 branch must produce the same actionable message as the 5xx
    branch, not the raw ``OpenAI 400: ...``."""
    from app.exceptions import AppError

    p = _make_provider(available_models=["gpt-image-1.5"])
    body = "{'error': {'code': '', 'message': '模型 gpt-image-2 不存在'}}"

    try:
        _call_generate_with_references_for_400(p, body)
    except AppError as e:
        assert e.code == "IMAGE_PROVIDER_ERROR"
        assert "gpt-image-2" in e.message
        assert "gpt-image-1.5" in e.message
        assert "设置" in e.message
        assert "OpenAI 400" not in e.message
    else:
        raise AssertionError("expected AppError for a model missing from the proxy")
    assert p.client.models.list.await_count == 1


def test_edits_400_with_model_in_proxy_falls_back_to_generations():
    """Legacy preserved path: when the model IS on the proxy but
    /v1/images/edits can't serve it (e.g. proxy doesn't expose the
    edits endpoint), the caller should fall back to text-only —
    NOT emit a misleading 'switch model' message."""
    from app.adapters.image.base import UnsupportedReferenceMode

    # Proxy has the picked model, but for some reason edits returns 400.
    p = _make_provider(available_models=["gpt-image-2", "dall-e-3"])
    body = "{'error': {'code': 'model_not_found', 'message': 'edits not available'}}"

    try:
        _call_generate_with_references_for_400(p, body)
    except UnsupportedReferenceMode:
        pass
    else:
        raise AssertionError(
            "expected UnsupportedReferenceMode so callers can fall back "
            "to /v1/images/generations"
        )
    assert p.client.models.list.await_count == 1


def test_edits_400_when_models_probe_fails_keeps_legacy_fallback():
    """Defensive: if the disambiguation probe itself fails (proxy
    rejects /v1/models, network blip), we don't have evidence that
    the model is missing — fall back to the legacy behavior so a
    flaky probe doesn't turn an edits-endpoint quirk into a
    misleading 'switch model' message."""
    from app.adapters.image.base import UnsupportedReferenceMode

    p = _make_provider(available_models=None)  # probe raises → cached []
    body = "{'error': {'code': 'model_not_found', 'message': '模型 X 不存在'}}"

    try:
        _call_generate_with_references_for_400(p, body)
    except UnsupportedReferenceMode:
        pass
    else:
        raise AssertionError(
            "expected UnsupportedReferenceMode when the model probe fails"
        )
    assert p.client.models.list.await_count == 1
