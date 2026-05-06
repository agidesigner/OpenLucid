"""Pin the multipart wire format for /v1/images/edits.

Past breakage: the adapter sent reference images under the field name
``image[]`` (PHP-style array notation). Some OpenAI-compatible proxies
tolerate this, but the canonical OpenAI endpoint expects repeated
``image`` parts. A future refactor that flips this back would silently
fail on a real OpenAI key — these assertions catch that.

Reference: https://platform.openai.com/docs/api-reference/images/createEdit
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_edits_multipart_uses_repeated_image_field_not_image_array():
    """Multi-image edit must send N parts named ``image`` — not ``image[]``."""
    from app.adapters.image.base import GenerateWithReferencesRequest
    from app.adapters.image.gpt_image import GPTImageProvider

    provider = GPTImageProvider(api_key="sk-test", model="gpt-image-2")

    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            # 1×1 PNG, base64-encoded — minimum valid response shape.
            return {
                "data": [
                    {
                        "b64_json": (
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE"
                            "QVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
                        )
                    }
                ]
            }

        @property
        def text(self):
            return ""

    fake_post = AsyncMock(return_value=_FakeResponse())

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            self.post = fake_post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    with patch("httpx.AsyncClient", _FakeClient):
        req = GenerateWithReferencesRequest(
            prompt="test prompt with json word for OpenAI",
            references=[b"FAKE_PNG_BYTES_1", b"FAKE_PNG_BYTES_2", b"FAKE_PNG_BYTES_3"],
            aspect_ratio="9:16",
        )
        _run(provider.generate_with_references(req))

    # The httpx call kwargs include ``files=`` — assert its shape.
    fake_post.assert_called_once()
    files_arg = fake_post.call_args.kwargs.get("files")
    assert files_arg is not None, "post() must be called with files= for multipart"

    field_names = [pair[0] for pair in files_arg]

    # Critical: every part must be named ``image`` (singular). The old
    # ``image[]`` PHP-style notation gets rejected by OpenAI with 400.
    assert all(name == "image" for name in field_names), (
        f"All multipart parts must be named 'image', got {field_names}. "
        f"OpenAI's /v1/images/edits rejects 'image[]'."
    )
    # Equally critical: the parts ARE repeated, not collapsed into one —
    # send 3 references → 3 parts.
    assert len(field_names) == 3, (
        f"Expected 3 'image' parts for 3 references, got {len(field_names)}"
    )


def test_edits_multipart_mime_follows_actual_bytes_not_caller_label():
    """Reference bytes must be labeled by their real format.

    The service-layer compressor re-encodes large posters as JPEG.
    Previously the adapter labeled every part as PNG, which strict
    proxies / OpenAI's edits endpoint can reject. The fix sniffs magic
    bytes and picks the right mime + filename ext per reference.
    """
    from app.adapters.image.base import GenerateWithReferencesRequest
    from app.adapters.image.gpt_image import GPTImageProvider

    provider = GPTImageProvider(api_key="sk-test", model="gpt-image-2")

    # Hand-crafted minimum byte streams that the sniffer must recognize.
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="}]}

        @property
        def text(self):
            return ""

    captured: dict = {}

    async def _fake_post(_url, *_a, **kw):
        captured["files"] = kw.get("files")
        return _FakeResponse()

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            self.post = _fake_post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    with patch("httpx.AsyncClient", _FakeClient):
        req = GenerateWithReferencesRequest(
            prompt="poster json",
            references=[PNG_MAGIC, JPEG_MAGIC, PNG_MAGIC],
            aspect_ratio="9:16",
        )
        _run(provider.generate_with_references(req))

    files = captured["files"]
    # Each file is ("image", (filename, bytes, mime))
    triples = [pair[1] for pair in files]
    mimes = [t[2] for t in triples]
    exts = [t[0].rsplit(".", 1)[-1] for t in triples]

    assert mimes == ["image/png", "image/jpeg", "image/png"], (
        f"MIME must follow magic bytes, got {mimes}"
    )
    assert exts == ["png", "jpg", "png"], (
        f"Filename extension must match actual format, got {exts}"
    )


def test_edits_endpoint_url_is_v1_images_edits():
    """Pin the URL path so a refactor can't accidentally hit /generations."""
    from app.adapters.image.base import GenerateWithReferencesRequest
    from app.adapters.image.gpt_image import GPTImageProvider

    provider = GPTImageProvider(
        api_key="sk-test",
        base_url="https://api.example.com/v1",
        model="gpt-image-2",
    )

    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="}]}

        @property
        def text(self):
            return ""

    async def _fake_post(url, *_a, **_kw):
        captured["url"] = url
        return _FakeResponse()

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            self.post = _fake_post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    with patch("httpx.AsyncClient", _FakeClient):
        req = GenerateWithReferencesRequest(
            prompt="test prompt with json keyword",
            references=[b"FAKE_BYTES"],
            aspect_ratio="9:16",
        )
        _run(provider.generate_with_references(req))

    assert captured.get("url", "").endswith("/v1/images/edits"), (
        f"Expected /v1/images/edits, got {captured.get('url')}"
    )
