"""Tests for guest-mode access control.

Two layers of isolation:
- Pure-logic tests re-implement or import only the allowlist matcher
  (mirrors how test_security_fixes isolates _log_task_exception).
- Service tests exercise guest_access_service with a hashing sanity check.

End-to-end HTTP tests would need a live Postgres; those are deferred to
the manual verification checklist in the plan file.
"""
from __future__ import annotations

import pytest


# ── 1. GUEST_WRITE_ALLOWLIST matcher ────────────────────────────────────


class TestGuestWriteAllowlist:
    """The middleware rule: GET is always allowed; non-GET must match one
    of the (method, path_prefix) tuples. Anything else → 403."""

    @staticmethod
    def _allowed(method: str, path: str) -> bool:
        # Copy of _guest_write_allowed from app.main. Keeping the test
        # self-contained avoids importing the FastAPI app (which in turn
        # mounts StaticFiles and tries to stat uploads/).
        ALLOWLIST = (
            ("POST", "/api/v1/ai/extract-text"),
            ("POST", "/api/v1/ai/infer-offer-knowledge"),
            ("POST", "/api/v1/ai/infer-offer-knowledge-stream"),
            ("POST", "/api/v1/apps/"),
            ("POST", "/api/v1/creations"),
            ("POST", "/api/v1/videos"),
            ("PATCH", "/api/v1/topic-plans/"),
            ("POST", "/api/v1/feedback"),
            ("POST", "/api/v1/auth/signout"),
        )
        if method == "GET":
            return True
        return any(method == m and path.startswith(p) for m, p in ALLOWLIST)

    def test_get_always_allowed(self):
        # Even to an endpoint that would be blocked for POST
        assert self._allowed("GET", "/api/v1/knowledge") is True
        assert self._allowed("GET", "/api/v1/settings/llm") is True
        assert self._allowed("GET", "/api/v1/merchants") is True

    def test_script_writer_app_allowed(self):
        assert self._allowed("POST", "/api/v1/apps/script-writer/run") is True
        assert self._allowed("POST", "/api/v1/apps/kb-qa/run") is True

    def test_creation_save_allowed(self):
        assert self._allowed("POST", "/api/v1/creations") is True
        assert self._allowed("POST", "/api/v1/creations/abc-123/videos") is True

    def test_topic_plan_rating_allowed(self):
        assert self._allowed("PATCH", "/api/v1/topic-plans/abc-123/rating") is True

    def test_feedback_allowed(self):
        assert self._allowed("POST", "/api/v1/feedback") is True

    def test_kb_write_blocked(self):
        assert self._allowed("POST", "/api/v1/knowledge") is False
        assert self._allowed("POST", "/api/v1/knowledge/batch") is False
        assert self._allowed("PATCH", "/api/v1/knowledge/abc") is False
        assert self._allowed("DELETE", "/api/v1/knowledge/abc") is False

    def test_brandkit_write_blocked(self):
        assert self._allowed("POST", "/api/v1/brandkits") is False
        assert self._allowed("PATCH", "/api/v1/brandkits/abc") is False

    def test_settings_write_blocked(self):
        assert self._allowed("POST", "/api/v1/settings/llm") is False
        assert self._allowed("PUT", "/api/v1/settings/llm/scenes") is False
        assert self._allowed("DELETE", "/api/v1/settings/mcp-tokens/abc") is False

    def test_guest_toggle_itself_blocked(self):
        # Guest must never disable/re-enable their own access
        assert self._allowed("POST", "/api/v1/auth/guest") is False
        assert self._allowed("DELETE", "/api/v1/auth/guest") is False

    def test_offer_and_merchant_writes_blocked(self):
        assert self._allowed("POST", "/api/v1/offers") is False
        assert self._allowed("PATCH", "/api/v1/offers/abc") is False
        assert self._allowed("DELETE", "/api/v1/merchants/abc") is False

    def test_asset_writes_blocked(self):
        # Upload creates asset rows — blocked. extract-text (pure LLM) is allowed.
        assert self._allowed("POST", "/api/v1/assets") is False
        assert self._allowed("POST", "/api/v1/ai/extract-text") is True


# ── 2. guest_access_service hash determinism ───────────────────────────


class TestGuestAccessHashing:
    def test_hash_is_sha256_hex(self):
        from app.application.guest_access_service import _hash
        h = _hash("hello")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_deterministic(self):
        from app.application.guest_access_service import _hash
        assert _hash("abc") == _hash("abc")

    def test_hash_differs_per_token(self):
        from app.application.guest_access_service import _hash
        assert _hash("abc") != _hash("abd")

    def test_empty_token_is_not_falsy_stored(self):
        # An empty token still hashes, but verify() must special-case it
        from app.application import guest_access_service as svc
        # Directly check the no-op in verify()
        import asyncio

        async def _run():
            class _FakeDB:
                async def scalar(self, *a, **k):
                    raise AssertionError("verify must short-circuit on empty token")
            return await svc.verify(_FakeDB(), "")

        assert asyncio.run(_run()) is False


# ── 3. Middleware state contract ───────────────────────────────────────
# Regression guard: the middleware must set request.state.is_guest for every
# authenticated branch. Future refactors that forget to set it will leave
# owner routes incorrectly gated by `getattr(request.state, 'is_guest', ...)`
# defaults.


class TestMiddlewareStateContract:
    def test_require_owner_rejects_missing_uid(self):
        from app.api.deps import require_owner
        from fastapi import HTTPException
        import asyncio

        class _Req:
            class state:
                pass

        with pytest.raises(HTTPException) as exc:
            asyncio.run(require_owner(_Req()))
        assert exc.value.status_code == 403

    def test_require_owner_rejects_guest(self):
        from app.api.deps import require_owner
        from fastapi import HTTPException
        import asyncio

        class _Req:
            class state:
                user_id = "guest"

        with pytest.raises(HTTPException) as exc:
            asyncio.run(require_owner(_Req()))
        assert exc.value.status_code == 403

    def test_require_owner_allows_real_uid(self):
        from app.api.deps import require_owner
        import asyncio

        class _Req:
            class state:
                user_id = "00000000-0000-0000-0000-000000000001"

        assert asyncio.run(require_owner(_Req())) == "00000000-0000-0000-0000-000000000001"

    def test_require_owner_allows_api_token(self):
        """API-token callers (MCP/CLI) must still be able to hit owner-only endpoints."""
        from app.api.deps import require_owner
        import asyncio

        class _Req:
            class state:
                user_id = "api-token"

        assert asyncio.run(require_owner(_Req())) == "api-token"


# ── 4. Public base URL fallback ────────────────────────────────────────
# Leftover placeholder APP_URL values (nihao.com, example.com, ...) must
# not poison generated guest/reset links — fall back to the live request
# origin instead.


class TestPublicBaseUrl:
    def test_placeholder_appurl_falls_back_to_request(self):
        from app.libs import url_utils
        from app.config import settings as s

        original = s.APP_URL
        s.APP_URL = "http://nihao.com"
        try:
            class _Req:
                base_url = "http://192.168.1.5:8000/"
            assert url_utils.get_public_base_url(_Req()) == "http://192.168.1.5:8000"
        finally:
            s.APP_URL = original

    def test_empty_appurl_falls_back_to_request(self):
        from app.libs import url_utils
        from app.config import settings as s

        original = s.APP_URL
        s.APP_URL = ""
        try:
            class _Req:
                base_url = "http://localhost:8000/"
            assert url_utils.get_public_base_url(_Req()) == "http://localhost:8000"
        finally:
            s.APP_URL = original

    def test_configured_appurl_wins(self):
        from app.libs import url_utils
        from app.config import settings as s

        original = s.APP_URL
        s.APP_URL = "https://real.example-domain.test"
        try:
            class _Req:
                base_url = "http://localhost:8000/"
            # APP_URL wins (lets production still point agents at a public URL
            # even when the request came from an internal reverse-proxy host).
            assert url_utils.get_public_base_url(_Req()) == "https://real.example-domain.test"
        finally:
            s.APP_URL = original

    def test_no_request_returns_appurl_or_empty(self):
        from app.libs import url_utils
        from app.config import settings as s

        original = s.APP_URL
        s.APP_URL = ""
        try:
            assert url_utils.get_public_base_url(None) == ""
        finally:
            s.APP_URL = original
