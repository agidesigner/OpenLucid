"""Pin the auth-middleware sentinel handling against the class of
"non-UUID string reaches WHERE User.id == uid" 500s.

Real incident: server with no JWT secret configured ran in open-access
mode, where the middleware sets ``request.state.user_id = "no-auth"``.
The /auth/me endpoint only special-cased ``"guest"`` and ``"api-token"``,
so ``"no-auth"`` fell through to ``select(User).where(User.id == uid)``
and asyncpg crashed converting "no-auth" to UUID — 500 on every page
load that hit /auth/me.

These tests guard ALL non-UUID sentinel paths through ALL UUID-keyed
auth endpoints. Adding a new sentinel without updating one consumer
should fail at least one of these.
"""
from __future__ import annotations

import asyncio


def test_sentinel_constants_match_main_middleware_strings():
    """Source-of-truth check: the sentinel constants in auth.py MUST
    match the literal strings that main.py's middleware writes into
    ``request.state.user_id``. Drift here = recurrence of the 500 bug."""
    from app.api.auth import (
        NON_USER_SENTINELS,
        SENTINEL_API_TOKEN,
        SENTINEL_GUEST,
        SENTINEL_NO_AUTH,
    )
    assert SENTINEL_API_TOKEN == "api-token"
    assert SENTINEL_GUEST == "guest"
    assert SENTINEL_NO_AUTH == "no-auth"
    assert NON_USER_SENTINELS == frozenset({"api-token", "guest", "no-auth"})


def test_is_real_user_uid_rejects_all_sentinels_and_empty():
    """The guard helper must reject every sentinel + empty/None — the
    contract is "if this returns True, ``WHERE User.id == uid`` is
    safe to execute"."""
    from app.api.auth import is_real_user_uid

    assert is_real_user_uid(None) is False
    assert is_real_user_uid("") is False
    assert is_real_user_uid("api-token") is False
    assert is_real_user_uid("guest") is False
    assert is_real_user_uid("no-auth") is False
    # Real-looking UUID strings pass the guard (the helper doesn't
    # validate UUID format — that's asyncpg's job. The point is just
    # to reject the known-bad sentinels.)
    assert is_real_user_uid("3d82ad15-a36e-4ef7-8caa-826f33b0b387") is True


def test_me_endpoint_returns_401_for_no_auth_without_db_query():
    """no-auth (open-access mode, no JWT cookie) → /auth/me must
    return 401, NOT a friendly MeResponse.

    Real incident: returning ``MeResponse(is_guest=False, id=None)``
    let the WebUI render an owner-style dashboard for a signed-out
    browser (avatar visible, "settings" link in dropdown, no redirect
    to /signin). The 500 crash is still avoided because we never
    reach the DB query — we just return 401 instead of pretending
    to be a valid identity.

    Open-access is for MCP / API callers (Bearer tokens), not for
    the WebUI dashboard."""
    from fastapi import HTTPException

    from app.api.auth import me

    class _State:
        user_id = "no-auth"

    class _Req:
        state = _State()

    class _Db:
        async def execute(self, *_a, **_kw):
            raise AssertionError(
                "DB must NOT be queried for sentinel user_id — that path "
                "is exactly what produced the production 500."
            )

    try:
        asyncio.run(me(_Req(), _Db()))
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("expected HTTPException(401) for no-auth")


def test_me_endpoint_handles_guest_without_db_query():
    from app.api.auth import me

    class _State:
        user_id = "guest"

    class _Req:
        state = _State()

    class _Db:
        async def execute(self, *_a, **_kw):
            raise AssertionError("DB must NOT be queried for guest sentinel")

    result = asyncio.run(me(_Req(), _Db()))
    assert result.is_guest is True
    assert result.id is None


def test_me_endpoint_handles_api_token_without_db_query():
    from app.api.auth import me

    class _State:
        user_id = "api-token"

    class _Req:
        state = _State()

    class _Db:
        async def execute(self, *_a, **_kw):
            raise AssertionError("DB must NOT be queried for api-token sentinel")

    result = asyncio.run(me(_Req(), _Db()))
    assert result.is_guest is False
    assert result.id is None


def test_me_endpoint_rejects_unknown_non_uuid_string_without_db_query():
    """Future-proofing: if someone adds a new sentinel to the
    middleware (say, ``"oauth"``) but forgets to update /auth/me, we
    must NOT silently let it reach the DB and 500 — return 401."""
    from fastapi import HTTPException

    from app.api.auth import me

    class _State:
        user_id = "some-future-sentinel"

    class _Req:
        state = _State()

    class _Db:
        async def execute(self, *_a, **_kw):
            raise AssertionError("DB must NOT be queried for unknown sentinel")

    try:
        asyncio.run(me(_Req(), _Db()))
    except HTTPException as e:
        assert e.status_code == 401
        return
    raise AssertionError("expected HTTPException(401)")


def test_is_admin_path_blocks_settings_for_all_methods():
    """/api/v1/settings/* is admin for EVERY method — GET responses
    leak API keys / MCP tokens, and writes mutate the deployment.

    Real incident: signed-out user on a fresh deployment could
    PUT /api/v1/settings/llm/* and rewrite the LLM config because
    open-access set ``user_id = "no-auth"`` for ALL paths."""
    from app.main import _is_admin_path

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        assert _is_admin_path(method, "/api/v1/settings/llm") is True
        assert _is_admin_path(method, "/api/v1/settings/mcp-tokens") is True
        assert _is_admin_path(method, "/api/v1/settings/llm/abc/models") is True


def test_is_admin_path_blocks_brandkit_and_knowledge_writes_only():
    """Brandkit / knowledge: writes blocked, reads allowed.
    Reads power content apps (kb_qa, brand-aware generation);
    writes mutate the deployment."""
    from app.main import _is_admin_path

    # Writes blocked
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert _is_admin_path(method, "/api/v1/brandkits/123") is True
        assert _is_admin_path(method, "/api/v1/knowledge/abc") is True
    # Reads allowed
    assert _is_admin_path("GET", "/api/v1/brandkits/123") is False
    assert _is_admin_path("GET", "/api/v1/knowledge/abc") is False


def test_is_admin_path_catches_collection_root_writes():
    """Real incident: ``POST /api/v1/brandkits`` (no trailing slash —
    the create-collection target) was passing through the no-auth
    fallback because ``startswith("/api/v1/brandkits/")`` only matched
    sub-paths. Confirmed by curl: HTTP 201 response writing as no-auth.

    The matcher now handles both forms: ``/api/v1/brandkits`` (bare
    collection) AND ``/api/v1/brandkits/abc`` (sub-resource). Both
    match the admin gate, neither bleeds across to unrelated paths
    like ``/api/v1/brandkits-other``.
    """
    from app.main import _is_admin_path

    # The exact collection-root paths that must be admin-write blocked
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert _is_admin_path(method, "/api/v1/brandkits") is True, \
            f"{method} /api/v1/brandkits (collection root) must be admin-write"
        assert _is_admin_path(method, "/api/v1/knowledge") is True, \
            f"{method} /api/v1/knowledge (collection root) must be admin-write"
    # Settings collection root (any method)
    for method in ("GET", "POST"):
        assert _is_admin_path(method, "/api/v1/settings") is True

    # No false positives: similarly-named paths must NOT be admin
    assert _is_admin_path("POST", "/api/v1/brandkits-something") is False
    assert _is_admin_path("POST", "/api/v1/knowledgeable") is False
    assert _is_admin_path("GET", "/api/v1/settings-export") is False


def test_is_admin_path_passes_normal_endpoints():
    """Content endpoints (offers, merchants, apps, topic-plans, etc.)
    must NOT be classified as admin — open-access users / guests need
    them to function."""
    from app.main import _is_admin_path

    for path in (
        "/api/v1/offers", "/api/v1/offers/abc",
        "/api/v1/merchants/abc",
        "/api/v1/apps/topic-studio/run",
        "/api/v1/topic-plans/abc",
        "/api/v1/creations",
        "/api/v1/auth/me",
        "/api/v1/ai/extract-text",
    ):
        for method in ("GET", "POST", "PUT", "DELETE"):
            assert _is_admin_path(method, path) is False, f"{method} {path} wrongly classified as admin"


def test_admin_paths_apply_to_both_guest_and_open_access():
    """Same admin rules used in BOTH the guest-cookie branch AND the
    open-access branch — single source of truth via ``_is_admin_path``.

    Source-level check: both branches must invoke the helper, otherwise
    one identity type can sneak past the gate the other respects."""
    import inspect

    from app import main

    src = inspect.getsource(main.auth_middleware)
    # Helper invoked at least twice (once per branch).
    assert src.count("_is_admin_path(") >= 2, (
        "_is_admin_path must be called from BOTH the guest path and the "
        "open-access fallback so the rules apply uniformly"
    )


def test_llm_options_endpoint_strips_secret_fields():
    """``/apps/llm-options`` exists so guest sessions can see what
    models the operator configured (so they can USE the deployment)
    without exposing api_key. The response schema must NEVER include
    api_key or base_url; if a future refactor accidentally re-exposes
    them via this endpoint, this test catches it."""
    from app.api.apps import LLMOptionResponse

    fields = set(LLMOptionResponse.model_fields.keys())
    # Required public fields
    assert "id" in fields
    assert "label" in fields
    assert "provider" in fields
    assert "model_name" in fields
    assert "is_active" in fields
    # Forbidden secret fields
    assert "api_key" not in fields, "api_key MUST NOT leak via /apps/llm-options"
    assert "base_url" not in fields, "base_url MUST NOT leak via /apps/llm-options"


def test_media_capabilities_get_whitelisted_for_guests():
    """``GET /api/v1/settings/media-capabilities`` returns dropdown
    metadata for the image/video/tts capability pickers — provider
    label, model_code, display label, but NO api_key. Guest mode
    needs read access for the creation UIs to populate (the B-roll
    model picker on creations.html). Pre-v1.3.5 the whole /settings/
    tree was locked, so guests saw "视频模型未设" when models existed.

    The PUT must STILL be admin-only — the read carries no secrets,
    but writes change deployment-wide defaults.
    """
    from app.main import _is_admin_path

    # GET is allowed for guests / no-auth
    assert _is_admin_path("GET", "/api/v1/settings/media-capabilities") is False
    # Writes still admin-locked
    for method in ("PUT", "POST", "PATCH", "DELETE"):
        assert _is_admin_path(method, "/api/v1/settings/media-capabilities") is True
    # Subpaths under /settings/media-capabilities/* (none currently,
    # but if added later they default back to admin-locked unless
    # explicitly added to the whitelist)
    assert _is_admin_path("GET", "/api/v1/settings/media-capabilities/edit") is True


def test_media_capabilities_response_carries_no_secrets():
    """Pinning the schema: the GET response shape must not include
    api_key / base_url / token. If a refactor accidentally adds one,
    the GET-whitelist becomes a leak vector."""
    from app.schemas.setting import MediaCapabilityOption

    forbidden = {"api_key", "base_url", "token", "secret"}
    fields = set(MediaCapabilityOption.model_fields.keys())
    leaked = forbidden & fields
    assert not leaked, f"MediaCapabilityOption must not expose {leaked}"


def test_llm_options_path_not_admin_locked():
    """The new endpoint sits at ``/api/v1/apps/llm-options`` —
    deliberately OUTSIDE the ``/api/v1/settings/`` prefix that
    ``_is_admin_path`` locks. Guest cookie sessions and the no-auth
    fallback both need to reach it (without it, guest mode loses the
    model picker entirely)."""
    from app.main import _is_admin_path

    for method in ("GET", "POST", "PUT", "DELETE"):
        assert _is_admin_path(method, "/api/v1/apps/llm-options") is False
    # And /settings/llm IS still admin-locked (the secret-bearing one).
    assert _is_admin_path("GET", "/api/v1/settings/llm") is True


def test_change_password_rejects_all_sentinels():
    """All three sentinels → 401 BEFORE any DB query.
    Pre-fix bug: api-token / guest / no-auth all crashed asyncpg
    trying to query ``WHERE User.id == "<sentinel>"``."""
    from fastapi import HTTPException

    from app.api.auth import change_password

    class _Body:
        current_password = "x"
        new_password = "y"

    class _Db:
        async def execute(self, *_a, **_kw):
            raise AssertionError("DB must NOT be queried for sentinel uid")

    for sentinel in ("api-token", "guest", "no-auth"):
        class _State:
            user_id = sentinel

        class _Req:
            state = _State()

        try:
            asyncio.run(change_password(_Body(), _Req(), _Db()))
        except HTTPException as e:
            assert e.status_code == 401, f"sentinel {sentinel!r} should reject with 401, got {e.status_code}"
            continue
        raise AssertionError(f"sentinel {sentinel!r} should have raised 401")
