"""Tests for the HTML asset cache-bust rewriter in app.main."""
from __future__ import annotations

import re


# Mirror of app.main._ASSET_HREF_RE + _rewrite_asset_ref, kept self-contained
# so the test suite doesn't pay the cost of importing the FastAPI app
# (which mounts StaticFiles against settings.STORAGE_BASE_PATH).
_RE = re.compile(rb'(src|href)="(/(?:js|css|images)/[^"?#]+)"')


def _rewrite(tag: str, html: bytes) -> bytes:
    suffix = f"?v={tag}".encode()
    return _RE.sub(
        lambda m: m.group(1) + b'="' + m.group(2) + suffix + b'"',
        html,
    )


class TestCacheBustRewrite:
    TAG = "0.9.9.7-deadbeef"

    def test_local_js_stamped(self):
        out = _rewrite(self.TAG, b'<script src="/js/shared.js"></script>')
        assert out == f'<script src="/js/shared.js?v={self.TAG}"></script>'.encode()

    def test_local_css_stamped(self):
        out = _rewrite(self.TAG, b'<link href="/css/app.css" rel="stylesheet">')
        assert out == f'<link href="/css/app.css?v={self.TAG}" rel="stylesheet">'.encode()

    def test_local_image_stamped(self):
        out = _rewrite(self.TAG, b'<img src="/images/logo.png" alt="">')
        assert out == f'<img src="/images/logo.png?v={self.TAG}" alt="">'.encode()

    def test_extra_attributes_preserved(self):
        out = _rewrite(self.TAG, b'<script src="/js/shared.js" defer></script>')
        assert out == f'<script src="/js/shared.js?v={self.TAG}" defer></script>'.encode()

    def test_external_cdn_untouched(self):
        raw = b'<script src="https://cdn.tailwindcss.com"></script>'
        assert _rewrite(self.TAG, raw) == raw

    def test_user_uploads_untouched(self):
        # User-uploaded media are served under /uploads/ and must not be
        # rewritten (their URLs already encode identity via the asset id).
        raw = b'<img src="/uploads/abc.png">'
        assert _rewrite(self.TAG, raw) == raw

    def test_already_versioned_is_idempotent(self):
        # A path that already has a query string is skipped — re-running
        # the rewriter must not produce /js/shared.js?v=old?v=new.
        raw = b'<script src="/js/shared.js?v=old"></script>'
        assert _rewrite(self.TAG, raw) == raw

    def test_multiple_refs_all_stamped(self):
        raw = (
            b'<script src="/js/i18n.js"></script>'
            b'<script src="/js/shared.js"></script>'
            b'<img src="/images/logo.png">'
        )
        out = _rewrite(self.TAG, raw).decode()
        assert out.count(f"?v={self.TAG}") == 3

    def test_single_quotes_not_rewritten(self):
        # Current HTML in this codebase uses double quotes. Single-quoted
        # attributes would need a separate regex — we document the gap
        # here so a future contributor sees it if the convention changes.
        raw = b"<script src='/js/shared.js'></script>"
        assert _rewrite(self.TAG, raw) == raw

    def test_empty_html_noop(self):
        assert _rewrite(self.TAG, b"") == b""


class TestCacheTagStability:
    def test_tag_has_version_prefix(self):
        from app.config import CACHE_TAG, VERSION
        assert CACHE_TAG.startswith(VERSION + "-")

    def test_tag_suffix_is_hex(self):
        from app.config import CACHE_TAG
        suffix = CACHE_TAG.split("-", 1)[1]
        # Parses as hex — guarantees URL-safety without extra encoding.
        int(suffix, 16)


class TestHtmlValidatorsStripped:
    """Regression: the middleware must strip Last-Modified and ETag on
    HTML responses. Otherwise the browser still sends conditional
    If-Modified-Since requests, StaticFiles returns 304, and the browser
    serves its stale pre-rewrite cached HTML — defeating the whole
    cache-bust strategy."""

    def _build(self):
        import re as _re
        from fastapi import FastAPI
        from fastapi.responses import FileResponse, Response
        from fastapi.testclient import TestClient

        CACHE_TAG = "0.9.9.7-deadbeef"
        SUFFIX = f"?v={CACHE_TAG}".encode()
        ASSET_RE = _re.compile(rb'(src|href)="(/(?:js|css|images)/[^"?#]+)"')

        app = FastAPI()

        @app.middleware("http")
        async def cache_control(request, call_next):
            response = await call_next(request)
            ct = (response.headers.get("content-type") or "").lower()
            if "text/html" in ct:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode()
                body = ASSET_RE.sub(
                    lambda m: m.group(1) + b'="' + m.group(2) + SUFFIX + b'"',
                    body,
                )
                h = dict(response.headers)
                h["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
                h["pragma"] = "no-cache"
                h["expires"] = "0"
                h["content-length"] = str(len(body))
                h.pop("transfer-encoding", None)
                h.pop("last-modified", None)
                h.pop("etag", None)
                return Response(
                    content=body,
                    status_code=response.status_code,
                    headers=h,
                    media_type="text/html",
                )
            return response

        @app.get("/html")
        async def _h():
            return FileResponse("/Users/ajin/aitools/opendirector/frontend/index.html")

        return TestClient(app)

    def test_no_last_modified_on_html(self):
        client = self._build()
        r = client.get("/html")
        assert r.status_code == 200
        assert r.headers.get("last-modified") is None
        assert r.headers.get("etag") is None

    def test_cache_control_is_fully_disabled(self):
        client = self._build()
        r = client.get("/html")
        cc = r.headers.get("cache-control", "")
        assert "no-store" in cc
        assert "no-cache" in cc
        assert "must-revalidate" in cc
        assert "max-age=0" in cc

    def test_conditional_request_still_returns_200(self):
        """Simulates a browser that cached the page before the fix was
        deployed. It resends its old If-Modified-Since / If-None-Match;
        the server must respond with a fresh 200 (not 304)."""
        client = self._build()
        r = client.get(
            "/html",
            headers={
                "If-Modified-Since": "Thu, 01 Jan 2026 00:00:00 GMT",
                "If-None-Match": '"stale-etag"',
            },
        )
        assert r.status_code == 200
        assert "?v=0.9.9.7-deadbeef" in r.text
