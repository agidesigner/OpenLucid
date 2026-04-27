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


def test_me_endpoint_handles_no_auth_without_db_query():
    """Open-access mode (no JWT secret) → ``user_id = "no-auth"``.
    /auth/me must return a friendly response, NOT crash on UUID cast."""
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

    result = asyncio.run(me(_Req(), _Db()))
    assert result.id is None
    assert result.is_guest is False
    assert result.is_active is True


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
