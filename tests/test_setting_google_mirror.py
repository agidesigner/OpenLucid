"""Pin the Gemini → Google media-provider mirror's safety contract.

Past breakage (caught in review): ``_sync_google_media_mirror`` queried
``provider == "google"`` and treated EVERY matching row as the auto-managed
mirror. That meant a user-created ``provider='google'`` row (e.g. someone
configured their own Google API key for video generation) would be:

  - silently overwritten with the Gemini LLM's key on the next sync,
  - silently DELETED when the user removed the Gemini LLM,
  - or trip ``MultipleResultsFound`` when both rows existed.

The fix is structural: every auto-managed row carries
``defaults._managed_by == "gemini_llm_mirror"`` and the sync only ever
reads / writes / deletes rows with that marker. These tests pin the
contract so a future "simpler" rewrite can't quietly re-introduce the bug.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeMpc:
    """Minimal MediaProviderConfig double — only the fields the sync
    function reads / writes."""

    def __init__(self, *, provider: str, label: str, defaults: dict, credentials: dict | None = None):
        self.provider = provider
        self.label = label
        self.defaults = defaults
        self.credentials = credentials or {}
        self.is_active = True


class _FakeLlm:
    def __init__(self, *, api_key: str, updated_at: str = "2026-05-05"):
        self.api_key = api_key
        self.provider = "gemini"
        self.updated_at = updated_at


class _FakeSession:
    """Pretend AsyncSession that returns canned LLMConfig + MPC rows.

    ``execute`` is sequenced: the function under test calls execute twice
    — first for the gemini lookup, then for the google MPC lookup. We
    record adds + deletes so tests can assert effects.
    """

    def __init__(self, gemini: _FakeLlm | None, mpc_rows: list[_FakeMpc]):
        self._gemini = gemini
        self._mpc_rows = mpc_rows
        self._call = 0
        self.added: list[_FakeMpc] = []
        self.deleted: list[_FakeMpc] = []

    async def execute(self, _stmt):  # type: ignore[no-untyped-def]
        self._call += 1
        # 1st call → LLMConfig.scalar_one_or_none()
        # 2nd call → MediaProviderConfig.scalars().all()
        if self._call == 1:
            return _CannedScalarOne(self._gemini)
        return _CannedScalarsAll(self._mpc_rows)

    def add(self, row):
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)


class _CannedScalarOne:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self


class _CannedScalarsAll:
    def __init__(self, values: list):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


# ── Tests ─────────────────────────────────────────────────────


def test_clean_model_name_trims_plain_model_id():
    from app.application.setting_service import _clean_model_name

    assert _clean_model_name("  deepseek-v4-pro  ") == "deepseek-v4-pro"
    assert _clean_model_name(None) is None


def test_clean_model_name_rejects_temperature_suffix():
    from fastapi import HTTPException

    from app.application.setting_service import _clean_model_name

    try:
        _clean_model_name("claude-opus-4-7_0.2")
    except HTTPException as e:
        assert e.status_code == 422
        assert "temperature value" in str(e.detail)
        assert "claude-opus-4-7" in str(e.detail)
    else:
        raise AssertionError("expected HTTPException for model id with _0.2 suffix")


def test_update_media_capability_rejects_invalid_provider_uuid():
    from fastapi import HTTPException

    from app.application.setting_service import update_media_capability_configs
    from app.schemas.setting import MediaCapabilitiesUpdateRequest, MediaCapabilityUpdate

    class _Db:
        async def get(self, *_a, **_kw):
            raise AssertionError("invalid UUID must be rejected before DB lookup")

        async def execute(self, *_a, **_kw):
            raise AssertionError("invalid UUID must be rejected before upsert")

    data = MediaCapabilitiesUpdateRequest(updates=[
        MediaCapabilityUpdate(
            capability="image_gen",
            provider_config_id="not-a-uuid",
            model_code="seedream-4",
        )
    ])

    try:
        _run(update_media_capability_configs(_Db(), data))
    except HTTPException as e:
        assert e.status_code == 422
        assert "Invalid provider_config_id" in str(e.detail)
    else:
        raise AssertionError("expected HTTPException for invalid provider_config_id")


def test_update_media_capability_rejects_missing_provider_before_fk():
    from fastapi import HTTPException

    from app.application.setting_service import update_media_capability_configs
    from app.schemas.setting import MediaCapabilitiesUpdateRequest, MediaCapabilityUpdate

    class _Db:
        get_calls = 0

        async def get(self, *_a, **_kw):
            self.get_calls += 1
            return None

        async def execute(self, *_a, **_kw):
            raise AssertionError("missing provider must be rejected before FK upsert")

    db = _Db()
    data = MediaCapabilitiesUpdateRequest(updates=[
        MediaCapabilityUpdate(
            capability="image_gen",
            provider_config_id="00000000-0000-0000-0000-000000000123",
            model_code="seedream-4",
        )
    ])

    try:
        _run(update_media_capability_configs(db, data))
    except HTTPException as e:
        assert e.status_code == 409
        assert "no longer exists" in str(e.detail)
        assert db.get_calls == 1
    else:
        raise AssertionError("expected HTTPException for missing provider_config_id")


_MIRROR_DEFAULTS = {"_managed_by": "gemini_llm_mirror", "aspect_ratio": "portrait"}


def test_sync_creates_mirror_when_no_google_row_exists():
    """First-time setup: gemini configured, no google row → add a mirror."""
    from app.application.setting_service import _sync_google_media_mirror

    db = _FakeSession(gemini=_FakeLlm(api_key="GEM-KEY"), mpc_rows=[])
    _run(_sync_google_media_mirror(db))

    assert len(db.added) == 1
    new = db.added[0]
    assert new.provider == "google"
    assert new.credentials == {"api_key": "GEM-KEY"}
    assert new.defaults.get("_managed_by") == "gemini_llm_mirror"


def test_sync_does_not_touch_user_managed_google_row():
    """A user's own ``provider='google'`` row (no _managed_by marker) must
    survive an LLM CRUD that triggers the sync. The mirror is a separate
    new row, not an overwrite of the user's credentials."""
    from app.application.setting_service import _sync_google_media_mirror

    user_row = _FakeMpc(
        provider="google",
        label="My own Google key",
        defaults={"aspect_ratio": "landscape"},
        credentials={"api_key": "USER-KEY"},
    )
    db = _FakeSession(gemini=_FakeLlm(api_key="GEM-KEY"), mpc_rows=[user_row])
    _run(_sync_google_media_mirror(db))

    # User's row must NOT be modified.
    assert user_row.credentials == {"api_key": "USER-KEY"}
    assert user_row.label == "My own Google key"
    assert "_managed_by" not in user_row.defaults
    assert user_row not in db.deleted

    # A SEPARATE mirror row should be added since no managed row existed.
    assert len(db.added) == 1
    assert db.added[0].defaults.get("_managed_by") == "gemini_llm_mirror"


def test_sync_only_deletes_managed_rows_when_gemini_removed():
    """Removing the Gemini LLM must drop ONLY the auto-managed mirror.
    Hand-rolled google rows survive."""
    from app.application.setting_service import _sync_google_media_mirror

    user_row = _FakeMpc(
        provider="google",
        label="My own Google key",
        defaults={"aspect_ratio": "landscape"},
    )
    mirror_row = _FakeMpc(
        provider="google",
        label="Google (linked from LLM Gemini)",
        defaults=dict(_MIRROR_DEFAULTS),
    )
    db = _FakeSession(gemini=None, mpc_rows=[user_row, mirror_row])
    _run(_sync_google_media_mirror(db))

    assert mirror_row in db.deleted
    assert user_row not in db.deleted
    assert db.added == []


def test_sync_dedups_multiple_managed_rows():
    """Defensive: if somehow two managed rows exist (race / earlier bug),
    sync keeps the first updated, deletes the rest. No throw."""
    from app.application.setting_service import _sync_google_media_mirror

    a = _FakeMpc(
        provider="google",
        label="Google (linked from LLM Gemini)",
        defaults=dict(_MIRROR_DEFAULTS),
    )
    b = _FakeMpc(
        provider="google",
        label="Google (linked from LLM Gemini)",
        defaults=dict(_MIRROR_DEFAULTS),
    )
    db = _FakeSession(gemini=_FakeLlm(api_key="GEM-KEY"), mpc_rows=[a, b])
    _run(_sync_google_media_mirror(db))

    # First gets refreshed credentials, second gets dropped.
    assert a.credentials == {"api_key": "GEM-KEY"}
    assert b in db.deleted
    assert db.added == []


def test_sync_updates_mirror_credentials_when_gemini_key_changes():
    """Rotating the Gemini key updates the existing mirror row in place
    (no new row, no delete-then-add)."""
    from app.application.setting_service import _sync_google_media_mirror

    mirror_row = _FakeMpc(
        provider="google",
        label="Google (linked from LLM Gemini)",
        defaults=dict(_MIRROR_DEFAULTS),
        credentials={"api_key": "OLD-KEY"},
    )
    db = _FakeSession(gemini=_FakeLlm(api_key="NEW-KEY"), mpc_rows=[mirror_row])
    _run(_sync_google_media_mirror(db))

    assert mirror_row.credentials == {"api_key": "NEW-KEY"}
    assert mirror_row.defaults.get("_managed_by") == "gemini_llm_mirror"
    assert db.added == []
    assert db.deleted == []


# ── OpenAI Image mirror ──
#
# Same safety contract as the Gemini mirror — these tests pin it for
# the second auto-managed mirror so a unified refactor can't quietly
# diverge their behaviors.


_OPENAI_MIRROR_DEFAULTS = {"_managed_by": "openai_llm_mirror"}


class _FakeOpenAILlm:
    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.provider = "openai"
        self.is_active = True


def test_openai_sync_creates_mirror_when_no_openai_image_row_exists():
    from app.application.setting_service import _sync_openai_image_mirror

    db = _FakeSession(gemini=_FakeOpenAILlm(api_key="sk-test"), mpc_rows=[])
    _run(_sync_openai_image_mirror(db))

    assert len(db.added) == 1
    new = db.added[0]
    assert new.provider == "openai_image"
    assert new.credentials.get("api_key") == "sk-test"
    assert new.defaults.get("_managed_by") == "openai_llm_mirror"


def test_openai_sync_does_not_touch_user_managed_openai_image_row():
    """A user-created ``provider='openai_image'`` row (no
    ``_managed_by`` marker) must survive an OpenAI LLM CRUD that
    triggers the sync. Same isolation as the Gemini mirror."""
    from app.application.setting_service import _sync_openai_image_mirror

    user_row = _FakeMpc(
        provider="openai_image",
        label="My image-only OpenAI key",
        defaults={},
        credentials={"api_key": "sk-user"},
    )
    db = _FakeSession(gemini=_FakeOpenAILlm(api_key="sk-llm"), mpc_rows=[user_row])
    _run(_sync_openai_image_mirror(db))

    assert user_row.credentials == {"api_key": "sk-user"}
    assert user_row not in db.deleted
    # A separate mirror row created in addition.
    assert len(db.added) == 1
    assert db.added[0].defaults.get("_managed_by") == "openai_llm_mirror"


def test_openai_sync_only_deletes_managed_rows_when_openai_llm_removed():
    from app.application.setting_service import _sync_openai_image_mirror

    user_row = _FakeMpc(
        provider="openai_image",
        label="My image-only OpenAI key",
        defaults={},
    )
    mirror_row = _FakeMpc(
        provider="openai_image",
        label="OpenAI Image (linked from LLM OpenAI)",
        defaults=dict(_OPENAI_MIRROR_DEFAULTS),
    )
    db = _FakeSession(gemini=None, mpc_rows=[user_row, mirror_row])
    _run(_sync_openai_image_mirror(db))

    assert mirror_row in db.deleted
    assert user_row not in db.deleted
    assert db.added == []
