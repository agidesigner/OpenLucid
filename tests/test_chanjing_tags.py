"""Pin the chanjing tag-dictionary endpoint contract + the service-layer
TTL cache and invalidation behavior.

Why this file exists: the picker UX wants chip-style filters ("场景:
商务/户外/厨房" + "年龄: 青年/中年/老年"). The data flows like this:

    /open/v1/common/tag_list?business_type=1   →   chanjing.list_avatar_tags
    AvatarItem.extras.tag_ids                  →   join key for chip filtering
    GET /api/v1/media-providers/{id}/avatar-tags  →   service-cached endpoint

These tests fence the two ends — the upstream parsing and the cache —
so the chip UI can rely on a stable contract.
"""
from __future__ import annotations

import asyncio
import time
import uuid


def _install_tag_request(provider, *, by_business_type: dict[int, list[dict]]):
    """Patch _request to serve tag_list responses keyed by business_type
    so a single test can verify both avatar (1) and voice (2) calls."""
    captured: list[dict] = []

    async def _fake_request(self, method, path, params=None, json_body=None, **kw):
        captured.append({"path": path, "params": params or {}})
        bt = (params or {}).get("business_type")
        return {"code": 0, "data": {"list": by_business_type.get(bt, [])}}

    type(provider)._request = _fake_request  # type: ignore[assignment]
    return captured


def _category(cat_id: int, cat_name: str, tags: list[dict]) -> dict:
    """Shape one outer category from the chanjing tag_list response."""
    return {
        "id": cat_id,
        "name": cat_name,
        "business_type": 1,
        "weight": 0,
        "tag_child_count": len(tags),
        "tag_list": tags,
    }


def _tag(tag_id: int, name: str, *, parent_id: int = 0, level: int = 1) -> dict:
    return {
        "id": tag_id,
        "name": name,
        "category_id": 1,
        "parent_id": parent_id,
        "level": level,
        "weight": 0,
        "status": 1,
    }


def test_list_avatar_tags_calls_business_type_1():
    """Avatars use business_type=1 per chanjing docs. A wrong code
    here would silently pull the voice taxonomy onto avatar chips."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_tag_request(p, by_business_type={
        1: [_category(1, "场景", [_tag(10, "商务"), _tag(11, "户外")])],
    })

    out = asyncio.run(p.list_avatar_tags())
    assert len(captured) == 1
    assert captured[0]["path"] == "/open/v1/common/tag_list"
    assert captured[0]["params"]["business_type"] == 1
    # Adapter returns plain dicts; service layer normalizes to schemas.
    assert out[0]["id"] == "1"
    assert out[0]["name"] == "场景"
    assert [t["name"] for t in out[0]["tags"]] == ["商务", "户外"]


def test_list_voice_tags_calls_business_type_2():
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_tag_request(p, by_business_type={
        2: [_category(5, "音色风格", [_tag(50, "亲切")])],
    })

    out = asyncio.run(p.list_voice_tags())
    assert captured[0]["params"]["business_type"] == 2
    assert out[0]["name"] == "音色风格"
    assert out[0]["tags"][0]["name"] == "亲切"


def test_parent_id_zero_normalized_to_none():
    """Chanjing uses parent_id=0 to mean 'root tag' — the schema treats
    None as the canonical 'no parent', so 0 must be normalized so the
    frontend doesn't have to special-case both."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_tag_request(p, by_business_type={
        1: [_category(1, "场景", [
            _tag(10, "商务", parent_id=0),
            _tag(11, "会议", parent_id=10, level=2),
        ])],
    })

    out = asyncio.run(p.list_avatar_tags())
    tags = out[0]["tags"]
    assert tags[0]["parent_id"] is None       # root
    assert tags[1]["parent_id"] == "10"       # nested under "商务"


def test_normalize_tag_categories_to_pydantic_shape():
    """The service layer wraps adapter dicts in TagCategory/TagOption.
    Pin the wrapper so the API response shape stays stable."""
    from app.application.media_provider_service import _normalize_tag_categories

    raw = [{
        "id": "1",
        "name": "场景",
        "tags": [
            {"id": "10", "name": "商务", "parent_id": None},
            {"id": "11", "name": "会议", "parent_id": "10"},
        ],
    }]
    result = _normalize_tag_categories(raw)
    assert len(result) == 1
    assert result[0].id == "1"
    assert result[0].name == "场景"
    assert [t.name for t in result[0].tags] == ["商务", "会议"]
    assert result[0].tags[0].parent_id is None
    assert result[0].tags[1].parent_id == "10"


def test_tag_cache_short_circuits_within_ttl(monkeypatch):
    """Second call inside the TTL window must NOT touch the upstream
    provider — the picker opens often, the dictionary moves rarely."""
    from app.application import media_provider_service as svc

    fake_id = uuid.uuid4()
    call_count = {"n": 0}

    class _FakeProvider:
        async def list_avatar_tags(self):
            call_count["n"] += 1
            return [{"id": "1", "name": "场景", "tags": []}]

    class _FakeConfig:
        def __init__(self):
            self.id = fake_id
            self.provider = "chanjing"
            self.credentials = {}

    class _FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return _FakeConfig()

    monkeypatch.setattr(svc, "MediaProviderRepository", _FakeRepo)
    monkeypatch.setattr(svc, "get_video_provider", lambda *a, **kw: _FakeProvider())
    # Clear cache for this id so the test starts from a clean slate.
    svc._invalidate_tag_cache(fake_id)

    asyncio.run(svc.list_avatar_tags_for_config(db=object(), config_id=fake_id))
    asyncio.run(svc.list_avatar_tags_for_config(db=object(), config_id=fake_id))
    assert call_count["n"] == 1, "second call must come from cache, not upstream"


def test_tag_cache_expires_after_ttl(monkeypatch):
    """Past the TTL the cache must miss and re-fetch — otherwise tag
    dictionary edits on the chanjing side never propagate without a
    process restart."""
    from app.application import media_provider_service as svc

    fake_id = uuid.uuid4()
    call_count = {"n": 0}

    class _FakeProvider:
        async def list_avatar_tags(self):
            call_count["n"] += 1
            return []

    class _FakeConfig:
        def __init__(self):
            self.provider = "chanjing"
            self.credentials = {}

    class _FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return _FakeConfig()

    monkeypatch.setattr(svc, "MediaProviderRepository", _FakeRepo)
    monkeypatch.setattr(svc, "get_video_provider", lambda *a, **kw: _FakeProvider())
    svc._invalidate_tag_cache(fake_id)

    base_t = 1_000_000.0
    monkeypatch.setattr(svc.time, "time", lambda: base_t)
    asyncio.run(svc.list_avatar_tags_for_config(db=object(), config_id=fake_id))

    # Jump past the TTL — cache entry must be considered stale.
    monkeypatch.setattr(svc.time, "time", lambda: base_t + svc._TAG_CACHE_TTL_SECONDS + 1)
    asyncio.run(svc.list_avatar_tags_for_config(db=object(), config_id=fake_id))
    assert call_count["n"] == 2


def test_tag_cache_invalidation_on_credential_change():
    """When a config's credentials change we may now be talking to a
    different chanjing tenant — its tag dictionary may differ. Cached
    entries from the old tenant must be dropped."""
    from app.application import media_provider_service as svc
    from app.schemas.media_provider import TagCategory

    fake_id = uuid.uuid4()
    svc._TAG_CACHE[(fake_id, "avatar")] = (time.time(), [
        TagCategory(id="1", name="场景", tags=[]),
    ])
    svc._TAG_CACHE[(fake_id, "voice")] = (time.time(), [])
    assert (fake_id, "avatar") in svc._TAG_CACHE

    svc._invalidate_tag_cache(fake_id)

    assert (fake_id, "avatar") not in svc._TAG_CACHE
    assert (fake_id, "voice") not in svc._TAG_CACHE


def test_avatar_tags_synthetic_survives_real_fetch_failure():
    """If chanjing's /common/tag_list call raises (auth scope, network
    blip, endpoint disabled on the account), the picker must STILL get
    the synthetic gender/age/aspect chips — those are independent of
    the upstream API. Without this resilience the chip bar disappears
    completely whenever the upstream stutters."""
    from app.adapters.video.chanjing import ChanjingVideoProvider
    from app.exceptions import AppError

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    async def _failing_request(self, method, path, params=None, json_body=None, **kw):
        raise AppError("CHANJING_HTTP_ERROR", "simulated upstream 502", 502)

    type(p)._request = _failing_request  # type: ignore[assignment]

    cats = asyncio.run(p.list_avatar_tags())
    cat_ids = {c["id"] for c in cats}
    # Real categories drop out, synthetic survives.
    assert cat_ids == {"gender", "age", "figure", "aspect"}


def test_voice_tags_synthetic_survives_real_fetch_failure():
    """Same fault-tolerance for voices."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    async def _failing_request(self, method, path, params=None, json_body=None, **kw):
        raise RuntimeError("simulated network error")

    type(p)._request = _failing_request  # type: ignore[assignment]

    cats = asyncio.run(p.list_voice_tags())
    cat_ids = {c["id"] for c in cats}
    assert cat_ids == {"gender", "age", "language"}


def test_jogg_tags_fallback_to_empty(monkeypatch):
    """jogg has no tag taxonomy. The endpoint must return [] (not 404)
    so the picker JS can render zero chip groups for jogg configs
    without branching on provider."""
    from app.application import media_provider_service as svc

    fake_id = uuid.uuid4()

    class _JoggLike:
        # No list_avatar_tags / list_voice_tags methods.
        pass

    class _FakeConfig:
        def __init__(self):
            self.provider = "jogg"
            self.credentials = {}

    class _FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_id(self, _id):
            return _FakeConfig()

    monkeypatch.setattr(svc, "MediaProviderRepository", _FakeRepo)
    monkeypatch.setattr(svc, "get_video_provider", lambda *a, **kw: _JoggLike())
    svc._invalidate_tag_cache(fake_id)

    out = asyncio.run(svc.list_avatar_tags_for_config(db=object(), config_id=fake_id))
    assert out == []
