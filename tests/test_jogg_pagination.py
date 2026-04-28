"""Pin the jogg list_all_avatars / list_all_voices pagination contract.

Same bug class as chanjing: a single-page fetch under-represents the
provider's public library, because jogg paginates `/avatars/public` and
`/voices` via `page` + `page_size` and the picker has no UI to walk.

The existing ad-hoc 5-page-of-100 loop in `synthesize_speech` already
confirmed jogg pagination is real (it scans pages to resolve a voice_id
to its sample URL); these tests pin the formal `list_all_*` contract.
"""
from __future__ import annotations

import asyncio


def _install_paged_request(provider, *, avatars=None, voices=None):
    """Patch _request to serve avatars and/or voices in 1-indexed pages.

    Pages beyond the configured list return empty (mimics jogg's
    end-of-pagination signal — empty `avatars` / `voices` array).
    """
    captured: list[dict] = []

    async def _fake_request(self, method, path, params=None, json_body=None):
        captured.append({"path": path, "params": params or {}})
        page = (params or {}).get("page", 1)
        idx = page - 1
        if path == "/avatars/public":
            pages = avatars or []
            items = pages[idx] if 0 <= idx < len(pages) else []
            return {"code": 0, "data": {"avatars": items}}
        if path == "/voices":
            pages = voices or []
            items = pages[idx] if 0 <= idx < len(pages) else []
            return {"code": 0, "data": {"voices": items}}
        raise AssertionError(f"unexpected path {path}")

    type(provider)._request = _fake_request  # type: ignore[assignment]
    return captured


def _avatar_item(item_id: str | int, name: str = "x") -> dict:
    return {
        "id": item_id,
        "name": name,
        "gender": "male",
        "cover_url": "https://x/c.png",
        "video_url": "https://x/v.mp4",
        "age": "adult",
        "aspect_ratio": 0,
    }


def _voice_item(voice_id: str, name: str = "v") -> dict:
    return {
        "voice_id": voice_id,
        "name": name,
        "gender": "female",
        "language": "en",
        "audio_url": "https://x/a.mp3",
        "age": "young",
    }


def test_list_all_avatars_walks_until_empty():
    from app.adapters.video.jogg import JoggVideoProvider

    p = JoggVideoProvider.__new__(JoggVideoProvider)
    p._api_key = "x"
    p._base_url = "https://api.jogg.ai/v2"
    captured = _install_paged_request(p, avatars=[
        [_avatar_item(i) for i in range(100)],            # page 1
        [_avatar_item(100 + i) for i in range(100)],      # page 2
        [_avatar_item(200 + i) for i in range(40)],       # page 3 (partial)
        # page 4 implicitly empty — terminates loop
    ])

    avatars = asyncio.run(p.list_all_avatars())
    assert len(avatars) == 240, f"expected 240 across 3 pages, got {len(avatars)}"
    pages_requested = [c["params"]["page"] for c in captured]
    assert pages_requested == [1, 2, 3, 4]


def test_list_all_avatars_dedupes_across_pages():
    from app.adapters.video.jogg import JoggVideoProvider

    p = JoggVideoProvider.__new__(JoggVideoProvider)
    p._api_key = "x"
    p._base_url = "https://api.jogg.ai/v2"
    _install_paged_request(p, avatars=[
        [_avatar_item("dup-1"), _avatar_item("a")],
        [_avatar_item("dup-1"), _avatar_item("b")],
    ])

    avatars = asyncio.run(p.list_all_avatars())
    assert [a.id for a in avatars] == ["dup-1", "a", "b"]


def test_list_all_avatars_respects_max_pages_cap():
    """Even if the server keeps serving fresh items, the loop stops
    at max_pages — guards against runaway scans."""
    from app.adapters.video.jogg import JoggVideoProvider

    p = JoggVideoProvider.__new__(JoggVideoProvider)
    p._api_key = "x"
    p._base_url = "https://api.jogg.ai/v2"
    counter = {"i": 0}

    async def _fake_request(self, method, path, params=None, json_body=None):
        counter["i"] += 1
        return {"code": 0, "data": {"avatars": [_avatar_item(f"x{counter['i']}")]}}

    type(p)._request = _fake_request  # type: ignore[assignment]

    avatars = asyncio.run(p.list_all_avatars(max_pages=3))
    assert len(avatars) == 3
    assert counter["i"] == 3


def test_list_all_avatars_terminates_on_all_duplicates_page():
    """Defensive: server bug returning the same page repeatedly must not
    spin to max_pages."""
    from app.adapters.video.jogg import JoggVideoProvider

    p = JoggVideoProvider.__new__(JoggVideoProvider)
    p._api_key = "x"
    p._base_url = "https://api.jogg.ai/v2"
    same_page = [_avatar_item("a"), _avatar_item("b")]
    captured = _install_paged_request(p, avatars=[same_page, same_page, same_page])

    avatars = asyncio.run(p.list_all_avatars(max_pages=10))
    assert len(avatars) == 2
    assert len(captured) == 2  # bailed on second page (all duplicates)


def test_list_all_voices_walks_pages():
    from app.adapters.video.jogg import JoggVideoProvider

    p = JoggVideoProvider.__new__(JoggVideoProvider)
    p._api_key = "x"
    p._base_url = "https://api.jogg.ai/v2"
    _install_paged_request(p, voices=[
        [_voice_item(f"v{i}") for i in range(100)],
        [_voice_item(f"w{i}") for i in range(50)],
    ])

    voices = asyncio.run(p.list_all_voices())
    assert len(voices) == 150


def test_jogg_accepts_but_ignores_sort_param():
    """Cross-provider parity: list_avatars must accept ``sort`` so the
    service layer can call all providers uniformly. Jogg's API has no
    sort param, so the value is silently ignored — but the call must
    not raise."""
    import inspect

    from app.adapters.video.jogg import JoggVideoProvider

    sig = inspect.signature(JoggVideoProvider.list_avatars)
    assert "sort" in sig.parameters, "jogg.list_avatars must accept sort for parity"

    sig_all = inspect.signature(JoggVideoProvider.list_all_avatars)
    assert "sort" in sig_all.parameters

    # Run a fetch with sort=hottest — must not error and must not
    # leak the sort into the request URL.
    p = JoggVideoProvider.__new__(JoggVideoProvider)
    p._api_key = "x"
    p._base_url = "https://api.jogg.ai/v2"
    captured: list[dict] = []

    async def _fake_request(self, method, path, params=None, json_body=None):
        captured.append({"path": path, "params": params or {}})
        return {"code": 0, "data": {"avatars": []}}

    type(p)._request = _fake_request  # type: ignore[assignment]
    asyncio.run(p.list_avatars(sort="hottest"))
    assert "sort" not in captured[0]["params"]


def test_web_api_picks_up_jogg_loop_via_getattr():
    """list_all_avatars_for_config detects provider.list_all_avatars via
    getattr — both chanjing and jogg must satisfy it now, so the picker
    auto-receives full libraries from either."""
    from app.adapters.video.chanjing import ChanjingVideoProvider
    from app.adapters.video.jogg import JoggVideoProvider

    assert callable(getattr(ChanjingVideoProvider, "list_all_avatars", None))
    assert callable(getattr(ChanjingVideoProvider, "list_all_voices", None))
    assert callable(getattr(JoggVideoProvider, "list_all_avatars", None))
    assert callable(getattr(JoggVideoProvider, "list_all_voices", None))
