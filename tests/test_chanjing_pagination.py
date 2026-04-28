"""Pin the chanjing list_all_avatars / list_all_voices pagination contract.

Why this matters: chanjing's /open/v1/list_common_dp endpoint silently caps
each response at ~50 items regardless of the `size` query parameter. A
single-page fetch therefore under-represents the public avatar library —
which is exactly the bug that left the picker stuck at 48 avatars even
though the chanjing console showed many more.

These tests pin the loop contract so a future "simplification" can't
quietly delete the pagination and re-introduce the cap.
"""
from __future__ import annotations

import asyncio


def _install_paged_request(provider, pages: list[list[dict]]):
    """Patch _request to return the given pages in order; the 1-indexed
    `page` query param decides which page to serve. Pages beyond the
    list are returned empty (mimics chanjing's end-of-pagination)."""
    captured: list[dict] = []

    async def _fake_request(self, method, path, params=None, json_body=None, **kw):
        captured.append({"path": path, "params": params or {}})
        page = (params or {}).get("page", 1)
        idx = page - 1
        items = pages[idx] if 0 <= idx < len(pages) else []
        return {"data": {"list": items}}

    type(provider)._request = _fake_request  # type: ignore[assignment]
    return captured


def _avatar_item(item_id: str, name: str = "x") -> dict:
    """Minimal chanjing avatar item shape (only fields list_avatars reads)."""
    return {
        "id": item_id,
        "name": name,
        "gender": "male",
        "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
        "audio_man_id": "voice-1",
    }


def _voice_item(item_id: str, name: str = "v") -> dict:
    return {
        "id": item_id,
        "name": name,
        "gender": "male",
        "lang": "zh-CN",
        "audition": "https://x/a.mp3",
    }


def test_list_all_avatars_walks_until_empty():
    """End-of-pagination signal is an empty `list` from the server."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_paged_request(p, [
        [_avatar_item(f"a{i}") for i in range(50)],   # page 1
        [_avatar_item(f"b{i}") for i in range(50)],   # page 2
        [_avatar_item(f"c{i}") for i in range(30)],   # page 3 (partial)
        # page 4 implicitly empty — terminates the loop
    ])

    avatars = asyncio.run(p.list_all_avatars())
    assert len(avatars) == 130, f"expected 130 across 3 pages, got {len(avatars)}"
    # The loop must have made exactly 4 requests (3 with data + 1 empty)
    assert len(captured) == 4
    pages_requested = [c["params"]["page"] for c in captured]
    assert pages_requested == [1, 2, 3, 4]


def test_list_all_avatars_dedupes_across_pages():
    """Cross-page id collision (chanjing has been observed to repeat
    items at page boundaries) must not double-count."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [
        [_avatar_item("dup-1"), _avatar_item("a")],
        [_avatar_item("dup-1"), _avatar_item("b")],  # dup-1 repeats
    ])

    avatars = asyncio.run(p.list_all_avatars())
    ids = [a.id for a in avatars]
    assert ids == ["dup-1", "a", "b"]


def test_list_all_avatars_terminates_on_all_duplicates_page():
    """Defensive: if a server bug returns the same page on every request,
    the loop must bail rather than spin to max_pages."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    same_page = [_avatar_item("a"), _avatar_item("b")]
    captured = _install_paged_request(p, [same_page, same_page, same_page])

    avatars = asyncio.run(p.list_all_avatars(max_pages=10))
    assert len(avatars) == 2
    # Loop must stop on page 2 (page 1 = new, page 2 = all dupes → bail).
    # 2 requests total, NOT 10.
    assert len(captured) == 2


def test_list_all_avatars_respects_max_pages_cap():
    """Runaway guard: even if the server keeps serving fresh items
    indefinitely, the loop must stop at max_pages."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    counter = {"i": 0}

    async def _fake_request(self, method, path, params=None, json_body=None, **kw):
        counter["i"] += 1
        # Every page returns one fresh, never-before-seen item.
        return {"data": {"list": [_avatar_item(f"x{counter['i']}")]}}

    type(p)._request = _fake_request  # type: ignore[assignment]

    avatars = asyncio.run(p.list_all_avatars(max_pages=5))
    assert len(avatars) == 5
    assert counter["i"] == 5


def test_list_all_voices_walks_pages():
    """Same pagination contract for voices — list_common_audio is
    capped the same way as list_common_dp."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [
        [_voice_item(f"v{i}") for i in range(40)],
        [_voice_item(f"w{i}") for i in range(20)],
    ])

    voices = asyncio.run(p.list_all_voices())
    assert len(voices) == 60


def test_validate_credentials_path_uses_single_page_not_loop():
    """The smoke-test in validate_credentials must NOT trigger the full
    pagination loop — that would turn a 'is the API key valid?' check
    into hundreds of HTTP calls. It calls list_avatars(page=1, size=1)
    directly, never list_all_avatars."""
    import inspect

    from app.application import media_provider_service as svc

    src = inspect.getsource(svc.validate_credentials)
    assert "list_avatars(page=1, page_size=1)" in src
    assert "list_all_avatars" not in src


def test_web_api_uses_all_pages_helper():
    """The web /avatars and /voices endpoints must route through the
    full-library helpers (list_all_*_for_config), not the paginated
    ones — otherwise the picker reverts to the 48-avatar bug."""
    import inspect

    from app.api import media_providers as mp

    src = inspect.getsource(mp)
    assert "list_all_avatars_for_config" in src
    assert "list_all_voices_for_config" in src


def test_mcp_path_remains_paginated():
    """MCP exposes pagination explicitly to agents (page / page_size
    args). It must keep using list_avatars_for_config (single page) so
    agents that walk pages themselves don't get duplicated work from a
    server-side full fetch."""
    import inspect

    from app import mcp_server

    src = inspect.getsource(mcp_server)
    # The MCP list_avatars tool must still call the paginated helper.
    assert "list_avatars_for_config" in src


# ── New-field passthrough + total_page-driven loop ──────────────────


def _install_paged_request_with_pageinfo(provider, pages: list[list[dict]]):
    """Like _install_paged_request but echoes a realistic page_info block
    (chanjing returns total_count + total_page on every response)."""
    captured: list[dict] = []
    total = sum(len(p) for p in pages)
    total_page = len(pages)

    async def _fake_request(self, method, path, params=None, json_body=None, **kw):
        captured.append({"path": path, "params": params or {}})
        page = (params or {}).get("page", 1)
        size = (params or {}).get("size", 50)
        idx = page - 1
        items = pages[idx] if 0 <= idx < len(pages) else []
        return {
            "data": {
                "list": items,
                "page_info": {
                    "page": page,
                    "size": size,
                    "total_count": total,
                    "total_page": total_page,
                },
            },
        }

    type(provider)._request = _fake_request  # type: ignore[assignment]
    return captured


def test_audio_name_and_preview_flow_into_extras():
    """Officially-paired voice metadata (name + audition URL) must reach
    the picker so cards can show "配 [▶ 林小妹]" without a second voices
    fetch. We were already capturing audio_man_id; name + preview are
    new in this slice."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[{
        "id": "av-1",
        "name": "Test",
        "gender": "female",
        "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
        "audio_man_id": "voice-9",
        "audio_name": "林小妹",
        "audio_preview": "https://x/preview.mp3",
    }]])

    avatars = asyncio.run(p.list_avatars())
    assert len(avatars) == 1
    extras = avatars[0].extras
    assert extras["paired_voice_id"] == "voice-9"
    assert extras["paired_voice_name"] == "林小妹"
    assert extras["paired_voice_preview_url"] == "https://x/preview.mp3"


def test_audio_extras_omitted_when_missing():
    """Items without paired-voice metadata must not invent empty keys —
    the frontend renders 'has paired voice' on key presence, so an empty
    string would falsely show a play button with no audio."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[{
        "id": "av-1",
        "name": "Test",
        "gender": "male",
        "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
        # No audio_* fields.
    }]])

    avatars = asyncio.run(p.list_avatars())
    extras = avatars[0].extras
    assert "paired_voice_id" not in extras
    assert "paired_voice_name" not in extras
    assert "paired_voice_preview_url" not in extras


def test_bg_replace_flag_flows_into_extras():
    """figures[0].bg_replace tells the picker which avatars work with
    custom backgrounds — we surface it as a boolean in extras."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[
        {
            "id": "av-yes",
            "name": "Replaceable",
            "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png", "bg_replace": True}],
        },
        {
            "id": "av-no",
            "name": "Locked",
            "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png", "bg_replace": False}],
        },
        {
            "id": "av-unknown",
            "name": "NoFlag",
            "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
        },
    ]])

    avatars = asyncio.run(p.list_avatars())
    by_id = {a.id: a for a in avatars}
    assert by_id["av-yes"].extras["bg_replace"] is True
    assert by_id["av-no"].extras["bg_replace"] is False
    # Missing flag → no key (don't invent a default; it'd be a lie).
    assert "bg_replace" not in by_id["av-unknown"].extras


def test_loop_terminates_at_server_total_page():
    """When chanjing returns total_page, the loop must stop there
    instead of probing one extra empty page — saves an HTTP round-trip
    on every full-library fetch."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_paged_request_with_pageinfo(p, [
        [_avatar_item(f"a{i}") for i in range(50)],
        [_avatar_item(f"b{i}") for i in range(50)],
        [_avatar_item(f"c{i}") for i in range(30)],
    ])

    avatars = asyncio.run(p.list_all_avatars())
    assert len(avatars) == 130
    # Exactly 3 requests — no probe for page 4.
    assert len(captured) == 3, f"expected 3 requests, got {len(captured)}"
    pages_requested = [c["params"]["page"] for c in captured]
    assert pages_requested == [1, 2, 3]


def test_tag_ids_merge_real_and_synthetic_tokens():
    """tag_ids carries BOTH chanjing's real ids (stringified) AND the
    cross-provider synthetic tokens. Single string-membership check
    powers the chip filter for both real chanjing tags and the
    universal gender/age/figure/aspect chips."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[{
        "id": "av-1",
        "name": "Test",
        "gender": "female",
        "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
        "tag_ids": [10, 22, 31],
        # No tag_names → no age token; no width/height → no aspect token.
    }]])

    avatars = asyncio.run(p.list_avatars())
    tokens = avatars[0].extras["tag_ids"]
    # Real chanjing ids stringified + synthetic gender + figure tokens.
    assert tokens == ["10", "22", "31", "gender:female", "figure:whole_body"]


def test_tag_ids_synthetic_only_when_no_real_tags():
    """Even without any real chanjing tags, the synthetic tokens still
    fire from gender/age/figure/aspect — the picker chip filter never
    goes blind for an avatar whose normalized fields are populated."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[
        {
            "id": "av-empty-tags",
            "name": "x",
            "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
            "tag_ids": [],
        },
        {
            "id": "av-missing-tags",
            "name": "y",
            "gender": "male",
            "figures": [{"type": "sit_body", "cover": "https://x/c.png"}],
            "width": 1080,
            "height": 1920,
            # no tag_ids field at all
        },
    ]])

    avatars = asyncio.run(p.list_avatars())
    by_id = {a.id: a for a in avatars}
    assert by_id["av-empty-tags"].extras["tag_ids"] == [
        "gender:male", "figure:whole_body",
    ]
    # Second avatar adds figure (sit_body) and aspect (from width/height).
    assert by_id["av-missing-tags"].extras["tag_ids"] == [
        "gender:male", "figure:sit_body", "aspect:portrait",
    ]


def test_tag_ids_omitted_when_no_signal_at_all():
    """If there is genuinely nothing to filter on (no gender, no age,
    no figure, no aspect, no real tags), don't emit an empty list —
    frontend treats key presence as 'has at least one filterable
    signal'."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[{
        "id": "av-blank",
        "name": "x",
        # no gender, no real figures.type, no tag_ids, no width/height
        "figures": [{"cover": "https://x/c.png"}],
    }]])

    avatars = asyncio.run(p.list_avatars())
    assert "tag_ids" not in avatars[0].extras


def test_native_aspect_ratio_buckets_correctly():
    """width/height must bucket into portrait / landscape / square so
    the picker can prioritise avatars matching the user's chosen
    output ratio. Buckets match jogg's encoding for cross-provider
    consistency."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[
        # 1080x1920 — clearly portrait (ratio 0.5625)
        {
            "id": "p", "name": "p", "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
            "width": 1080, "height": 1920,
        },
        # 1920x1080 — landscape (ratio 1.78)
        {
            "id": "l", "name": "l", "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
            "width": 1920, "height": 1080,
        },
        # 1080x1080 — square
        {
            "id": "s", "name": "s", "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
            "width": 1080, "height": 1080,
        },
        # 1024x1000 — within ±5% of 1:1 → still "square"
        {
            "id": "near-square", "name": "n", "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
            "width": 1024, "height": 1000,
        },
    ]])

    avatars = asyncio.run(p.list_avatars())
    by_id = {a.id: a for a in avatars}
    assert by_id["p"].extras["native_aspect_ratio"] == "portrait"
    assert by_id["l"].extras["native_aspect_ratio"] == "landscape"
    assert by_id["s"].extras["native_aspect_ratio"] == "square"
    assert by_id["near-square"].extras["native_aspect_ratio"] == "square"


def test_native_aspect_ratio_omitted_on_missing_or_invalid_dimensions():
    """Without trustworthy dimensions, omit the key — emitting a
    default would mislead the picker into filtering on bad data."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[
        {
            "id": "no-dims", "name": "x", "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
        },
        {
            "id": "zero-h", "name": "x", "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
            "width": 1920, "height": 0,
        },
        {
            "id": "non-int", "name": "x", "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
            "width": "1920", "height": "1080",  # string — provider bug
        },
    ]])

    avatars = asyncio.run(p.list_avatars())
    for a in avatars:
        assert "native_aspect_ratio" not in a.extras, \
            f"avatar {a.id} should have no native_aspect_ratio key"


def test_avatar_fetch_sends_latest_sort_by_default():
    """Default sort is ``latest`` (newest avatars first). Without an
    explicit sort chanjing returns ID-ascending order, which surfaces
    long-tail entries — the picker feels stale because the same
    neglected templates appear at the top every time. Pin so a future
    refactor can't silently drop the param.
    """
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_paged_request(p, [
        [_avatar_item("a"), _avatar_item("b")],
    ])

    asyncio.run(p.list_avatars())
    assert captured[0]["params"]["sort"] == "latest"
    assert captured[0]["params"]["page"] == 1
    assert captured[0]["params"]["size"] == 50


def test_avatar_fetch_passes_hottest_sort_when_requested():
    """User can switch to ``hottest`` (popularity ranking) — value
    flows through unchanged."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_paged_request(p, [
        [_avatar_item("a")],
    ])

    asyncio.run(p.list_avatars(sort="hottest"))
    assert captured[0]["params"]["sort"] == "hottest"


def test_avatar_fetch_omits_sort_for_default():
    """Passing ``sort=None`` means "let chanjing decide" — the param
    must be absent from the request, not stringified to "None"."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_paged_request(p, [
        [_avatar_item("a")],
    ])

    asyncio.run(p.list_avatars(sort=None))
    assert "sort" not in captured[0]["params"]


def test_list_all_avatars_propagates_sort():
    """The full-library walker must carry the sort through every page
    — otherwise switching to ``hottest`` would only re-order the first
    page and silently default-sort the rest."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_paged_request(p, [
        [_avatar_item(f"a{i}") for i in range(50)],
        [_avatar_item(f"b{i}") for i in range(20)],
    ])

    asyncio.run(p.list_all_avatars(sort="hottest"))
    assert all(c["params"]["sort"] == "hottest" for c in captured)


def test_circle_view_figure_dropped_picks_alternative():
    """Avatars with multiple figure variants — chanjing returns the
    same digital human in whole_body / sit_body / circle_view crops.
    The picker is for full-body talking-head video, not headshot
    stickers, so we drop circle_view and use the first remaining
    variant. Without this, picking a circle_view avatar produces a
    distorted output (chanjing stretches the headshot into a 9:16
    or 16:9 canvas)."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[
        {
            "id": "av-mixed",
            "name": "x",
            "gender": "female",
            "figures": [
                {"type": "circle_view", "cover": "https://x/circle.png", "width": 720, "height": 720},
                {"type": "whole_body", "cover": "https://x/whole.png", "width": 1080, "height": 1920, "bg_replace": True},
            ],
        },
    ]])

    avatars = asyncio.run(p.list_avatars())
    assert len(avatars) == 1
    a = avatars[0]
    # figure_type / preview / bg_replace must come from the chosen
    # non-circle_view figure, not figures[0].
    assert a.extras["figure_type"] == "whole_body"
    assert a.preview_image_url == "https://x/whole.png"
    assert a.extras["bg_replace"] is True


def test_circle_view_only_avatar_dropped_entirely():
    """If the only available figure is circle_view, the avatar is
    skipped — there's no full-body variant to render. Picker simply
    won't show it."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[
        {
            "id": "av-circle-only",
            "name": "x",
            "gender": "female",
            "figures": [
                {"type": "circle_view", "cover": "https://x/circle.png", "width": 720, "height": 720},
            ],
        },
        # Sanity: a normal whole_body avatar in the same response is
        # NOT dropped — the filter is per-figure, not page-wide.
        {
            "id": "av-normal",
            "name": "y",
            "gender": "male",
            "figures": [{"type": "whole_body", "cover": "https://x/n.png"}],
        },
    ]])

    avatars = asyncio.run(p.list_avatars())
    ids = [a.id for a in avatars]
    assert "av-circle-only" not in ids
    assert "av-normal" in ids


def test_native_aspect_ratio_falls_back_to_figures_dimensions():
    """When chanjing's top-level width/height are absent or zero (which
    we observe in production — many templates only populate dimensions
    on figures[]), the parser must read figures[0].width/height instead.
    Without this fallback the 9:16 / 16:9 toggle silently does nothing
    for chanjing avatars."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    _install_paged_request(p, [[
        # Top-level dims missing; figures[0] supplies them.
        {
            "id": "fig-portrait", "name": "p", "gender": "male",
            "figures": [{
                "type": "whole_body", "cover": "https://x/c.png",
                "width": 1080, "height": 1920,
            }],
        },
        # Top-level dims zero (chanjing's "unset" sentinel); figures[0] wins.
        {
            "id": "fig-landscape", "name": "l", "gender": "female",
            "width": 0, "height": 0,
            "figures": [{
                "type": "sit_body", "cover": "https://x/c.png",
                "width": 1920, "height": 1080,
            }],
        },
        # Top-level present and valid → still wins (no regression).
        {
            "id": "top-square", "name": "s", "gender": "male",
            "width": 1024, "height": 1024,
            "figures": [{
                # Use sit_body — circle_view would be filtered out
                # entirely by the picker, so it can't appear here.
                "type": "sit_body", "cover": "https://x/c.png",
                "width": 0, "height": 0,
            }],
        },
    ]])

    avatars = asyncio.run(p.list_avatars())
    by_id = {a.id: a for a in avatars}
    assert by_id["fig-portrait"].extras["native_aspect_ratio"] == "portrait"
    assert by_id["fig-landscape"].extras["native_aspect_ratio"] == "landscape"
    assert by_id["top-square"].extras["native_aspect_ratio"] == "square"


def test_loop_falls_back_to_probe_when_pageinfo_missing():
    """Backwards-compat: if chanjing ever drops page_info from the
    response, the loop must still terminate via the empty-page probe.
    The original test_list_all_avatars_walks_until_empty covers this
    path — restate it here to make the intent explicit."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"
    captured = _install_paged_request(p, [
        [_avatar_item("a"), _avatar_item("b")],
        # next page implicitly empty
    ])

    avatars = asyncio.run(p.list_all_avatars())
    assert len(avatars) == 2
    # 1 data page + 1 empty-page probe = 2 requests
    assert len(captured) == 2
