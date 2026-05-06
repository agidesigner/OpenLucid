"""Pin the article-cover suggest path's three load-bearing contracts:

  1. Platform → cover-aspect mapping covers the platforms content-studio
     actually exposes (zh + en), and unknown platforms fall back to a
     sensible default rather than raising.
  2. Tag-overlap selection is STRICT — no candidate, no result. Earlier
     drafts of ``_suggest_assets_by_tags`` reused the brief-suggest
     fallback that returned arbitrary "top by hook_score" assets when no
     tag matched, which meant the cover panel auto-selected unrelated
     reference images. The cover panel UX bets on auto-select being
     trustworthy — wrong auto-selects are worse than empty ones.
  3. ``ArticleCoverJobCreate`` accepts the brief-first fields the panel
     sends (brief / reference_asset_ids / extra_uploads / aspect).
     Schema regressions break the client silently — pin the field names.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 1. Aspect-by-platform ───────────────────────────────────────────


def test_aspect_map_covers_zh_publishing_platforms():
    from app.application.image_service import _aspect_for_platform

    # 公众号 — 长横版封面是公众号题图标准
    assert _aspect_for_platform("wechat_gzh") == "2.35:1"
    # 小红书 — 竖版图文卡片，3:4 是 feed 缩略图首选
    assert _aspect_for_platform("xiaohongshu") == "3:4"
    # 视频号 — 竖屏
    assert _aspect_for_platform("wechat_video") == "9:16"


def test_aspect_map_covers_en_publishing_platforms():
    from app.application.image_service import _aspect_for_platform

    # LinkedIn / Substack — OG card 1.91:1 (1200×627) is the spec
    assert _aspect_for_platform("linkedin") == "1.91:1"
    assert _aspect_for_platform("substack") == "1.91:1"
    # X (Twitter) cards — wide 16:9
    assert _aspect_for_platform("x_twitter") == "16:9"
    # Instagram carousel — portrait 4:5 maximizes feed real estate
    assert _aspect_for_platform("instagram_carousel") == "4:5"
    # Reddit — 4:3 thumbnail
    assert _aspect_for_platform("reddit") == "4:3"


def test_aspect_map_unknown_platform_falls_back_to_default():
    """A new / typo'd platform_id must not crash — return the default."""
    from app.application.image_service import _aspect_for_platform

    assert _aspect_for_platform(None) == "16:9"
    assert _aspect_for_platform("") == "16:9"
    assert _aspect_for_platform("some-future-platform") == "16:9"


# ── 2. Tag-overlap selection is strict ──────────────────────────────


class _FakeAsset:
    """Minimal Asset double — only the fields the suggester reads."""

    def __init__(
        self,
        *,
        asset_id: str,
        tags: dict,
        width: int = 1024,
        height: int = 1024,
        hook_score: float = 0.0,
    ):
        self.id = asset_id
        self.tags_json = tags
        self.metadata_json = {"width": width, "height": height}
        self.hook_score = hook_score


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeSession:
    """Returns canned asset rows for any execute() call.

    The function under test runs exactly one query — a SELECT against
    the offer's image assets — so we don't need to dispatch by stmt.
    """

    def __init__(self, assets):
        self._assets = assets

    async def execute(self, _stmt):
        return _FakeResult(self._assets)


def test_tag_overlap_picks_assets_with_matching_tags():
    from app.application.image_service import _suggest_assets_by_tags

    a1 = _FakeAsset(
        asset_id="a-001",
        tags={"subject": ["数字人", "讲师"], "scenario": ["直播间"]},
        hook_score=0.8,
    )
    a2 = _FakeAsset(
        asset_id="a-002",
        tags={"subject": ["产品图"], "scenario": ["桌面"]},
        hook_score=0.7,
    )
    db = _FakeSession([a1, a2])

    import uuid
    picked = _run(
        _suggest_assets_by_tags(
            db,
            offer_id=uuid.uuid4(),
            tags=["数字人", "直播间"],
            limit=2,
        )
    )
    assert "a-001" in picked
    assert "a-002" not in picked


def test_tag_overlap_returns_empty_when_no_match():
    """Strict contract: no tag overlap → empty list. Auto-selecting
    unrelated assets in the cover panel is worse than selecting nothing
    (the user was told these were "matched" — wrong matches mislead)."""
    from app.application.image_service import _suggest_assets_by_tags

    a1 = _FakeAsset(
        asset_id="a-001",
        tags={"subject": ["产品图"], "scenario": ["桌面"]},
        hook_score=0.95,  # high score — would be picked by hook_score fallback
    )
    db = _FakeSession([a1])

    import uuid
    picked = _run(
        _suggest_assets_by_tags(
            db,
            offer_id=uuid.uuid4(),
            tags=["完全无关词"],
            limit=2,
        )
    )
    assert picked == []


def test_tag_overlap_drops_logo_sized_assets():
    """Assets smaller than 600×600 get dropped — they're typically logos
    or icons, and using them as style references pollutes the model's
    composition signal (the brief flow has the same guard)."""
    from app.application.image_service import _suggest_assets_by_tags

    big = _FakeAsset(
        asset_id="big",
        tags={"subject": ["数字人"]},
        width=1200, height=1200,
    )
    tiny = _FakeAsset(
        asset_id="tiny",
        tags={"subject": ["数字人"]},
        width=128, height=128,
    )
    db = _FakeSession([big, tiny])

    import uuid
    picked = _run(
        _suggest_assets_by_tags(
            db,
            offer_id=uuid.uuid4(),
            tags=["数字人"],
            limit=5,
        )
    )
    assert "big" in picked
    assert "tiny" not in picked


def test_tag_overlap_no_tags_returns_empty():
    from app.application.image_service import _suggest_assets_by_tags

    db = _FakeSession([])
    import uuid
    picked = _run(
        _suggest_assets_by_tags(
            db, offer_id=uuid.uuid4(), tags=[], limit=2
        )
    )
    assert picked == []


# ── 3. ArticleCoverJobCreate brief-first fields ─────────────────────


def test_article_cover_schema_accepts_brief_first_fields():
    """Pin field names the cover panel posts. A schema rename would
    silently 422 the panel — these assertions catch that without
    spinning up the API."""
    from app.schemas.image_generation import ArticleCoverJobCreate

    body = {
        "brief": "在企业培训直播间场景中，数字人讲师正在分享 PPT",
        "aspect_ratio": "2.35:1",
        "reference_asset_ids": ["asset-1", "asset-2"],
        "extra_asset_ids": [],
        "extra_uploads": [
            {"upload_id": "tmp/image_refs/x/file.png", "role": "supplemental"}
        ],
    }
    parsed = ArticleCoverJobCreate.model_validate(body)
    assert parsed.brief == body["brief"]
    assert parsed.aspect_ratio == "2.35:1"
    assert parsed.reference_asset_ids == ["asset-1", "asset-2"]
    assert len(parsed.extra_uploads) == 1
    assert parsed.extra_uploads[0].upload_id == "tmp/image_refs/x/file.png"


def test_article_cover_schema_legacy_path_still_validates():
    """Backward compat: an older client posting only ``aspect_ratio`` +
    ``extra_prompt`` (the pre-cover-panel API shape) must still parse —
    the server's light path handles this case without references."""
    from app.schemas.image_generation import ArticleCoverJobCreate

    parsed = ArticleCoverJobCreate.model_validate(
        {"aspect_ratio": "16:9", "extra_prompt": "moody"}
    )
    assert parsed.brief is None
    assert parsed.reference_asset_ids == []
    assert parsed.extra_uploads == []


def test_article_cover_aspect_accepts_full_platform_set():
    """The schema's ArticleAspect literal must enumerate every aspect
    ``_aspect_for_platform`` can return. Otherwise the panel would
    surface a value the schema rejects (422)."""
    from app.schemas.image_generation import ArticleCoverJobCreate
    from app.application.image_service import (
        _COVER_ASPECT_BY_PLATFORM,
        _DEFAULT_COVER_ASPECT,
    )

    aspects = set(_COVER_ASPECT_BY_PLATFORM.values()) | {_DEFAULT_COVER_ASPECT}
    for asp in aspects:
        ArticleCoverJobCreate.model_validate({"aspect_ratio": asp})


def test_article_cover_caps_reference_lists():
    """Schema caps mirror BriefJobCreate so a direct API caller can't
    flood multipart upload size to the image provider."""
    from app.schemas.image_generation import ArticleCoverJobCreate
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        ArticleCoverJobCreate.model_validate(
            {"reference_asset_ids": [f"a{i}" for i in range(6)]}  # cap = 5
        )
    with pytest.raises(ValidationError):
        ArticleCoverJobCreate.model_validate(
            {"extra_asset_ids": [f"a{i}" for i in range(5)]}  # cap = 4
        )


# ── 4. Provider aspect maps don't fall back to portrait for wide ratios ──
#
# Regression caught in review: 1.91:1 / 2.35:1 weren't in any of the
# three provider size maps, and ``_SIZE_MAP.get(aspect, _SIZE_MAP["9:16"])``
# silently routed wide platform aspects (LinkedIn / 公众号 cover) into
# portrait 9:16 renders. These tests pin every aspect produced by
# ``_aspect_for_platform`` to a *landscape* size in every provider's
# map, so the fallback can never re-creep in.


def _is_landscape_size(width: int, height: int) -> bool:
    return width > height


def test_gpt_image_size_map_covers_all_platform_aspects():
    """Every aspect we hand a provider must be explicitly mapped — no
    fallback. And wide article-cover aspects must land on landscape
    sizes, not portrait."""
    from app.adapters.image.gpt_image import _SIZE_MAP
    from app.application.image_service import (
        _COVER_ASPECT_BY_PLATFORM,
        _DEFAULT_COVER_ASPECT,
    )

    aspects = set(_COVER_ASPECT_BY_PLATFORM.values()) | {_DEFAULT_COVER_ASPECT}
    for asp in aspects:
        assert asp in _SIZE_MAP, f"gpt_image missing aspect: {asp}"

    # Wide cover ratios must map to a landscape size, never portrait.
    for wide in ("1.91:1", "2.35:1", "16:9"):
        _, w, h = _SIZE_MAP[wide]
        assert _is_landscape_size(w, h), f"{wide} mapped to portrait {w}x{h}"


def test_google_aspect_map_covers_all_platform_aspects():
    from app.adapters.image.google_image import _ASPECT_MAP
    from app.application.image_service import (
        _COVER_ASPECT_BY_PLATFORM,
        _DEFAULT_COVER_ASPECT,
    )

    aspects = set(_COVER_ASPECT_BY_PLATFORM.values()) | {_DEFAULT_COVER_ASPECT}
    for asp in aspects:
        assert asp in _ASPECT_MAP, f"google missing aspect: {asp}"
    # Wide ratios must land on a landscape Gemini value.
    for wide in ("1.91:1", "2.35:1"):
        assert _ASPECT_MAP[wide] in ("16:9", "4:3", "3:2"), (
            f"google routed {wide} to non-landscape {_ASPECT_MAP[wide]}"
        )


def test_chanjing_aspect_map_covers_all_platform_aspects():
    from app.adapters.image.chanjing_image import _ASPECT_MAP
    from app.application.image_service import (
        _COVER_ASPECT_BY_PLATFORM,
        _DEFAULT_COVER_ASPECT,
    )

    aspects = set(_COVER_ASPECT_BY_PLATFORM.values()) | {_DEFAULT_COVER_ASPECT}
    for asp in aspects:
        assert asp in _ASPECT_MAP, f"chanjing missing aspect: {asp}"
    for wide in ("1.91:1", "2.35:1"):
        assert _ASPECT_MAP[wide] in ("16:9", "4:3"), (
            f"chanjing routed {wide} to non-landscape {_ASPECT_MAP[wide]}"
        )


# ── 5. Center-crop enforces the exact target aspect ─────────────────


def _make_solid_png_bytes(width: int, height: int) -> bytes:
    """Render a tiny solid-color PNG of the given size for crop tests."""
    import io
    from PIL import Image
    img = Image.new("RGB", (width, height), (200, 100, 50))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _read_size(image_bytes: bytes) -> tuple[int, int]:
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    img.load()
    return img.size


def test_crop_to_aspect_narrows_landscape_to_target():
    """16:9 source (1.78:1) cropped to 2.35:1 must come back at the
    target ratio — the visible bug that prompted post-process crop is
    公众号 covers being silently 16:9 instead of 2.35:1."""
    from app.application.image_service import _crop_image_to_aspect

    src = _make_solid_png_bytes(1536, 864)  # 16:9
    out = _crop_image_to_aspect(src, "2.35:1")
    w, h = _read_size(out)
    actual_ratio = w / h
    target = 2.35
    assert abs(actual_ratio - target) / target < 0.02


def test_crop_to_aspect_handles_portrait_source():
    """When source is taller than target, crop top+bottom (rare, but
    happens if the provider routes a wide cover to a portrait fallback
    we missed)."""
    from app.application.image_service import _crop_image_to_aspect

    src = _make_solid_png_bytes(1024, 1536)  # 2:3 portrait
    out = _crop_image_to_aspect(src, "1.91:1")
    w, h = _read_size(out)
    actual_ratio = w / h
    assert abs(actual_ratio - 1.91) / 1.91 < 0.02


def test_crop_to_aspect_is_idempotent_when_already_close():
    """Within 1% of target → return original bytes. Avoids re-encoding
    every cover that the provider already nailed."""
    from app.application.image_service import _crop_image_to_aspect

    src = _make_solid_png_bytes(1920, 1080)  # 16:9 = 1.778
    out = _crop_image_to_aspect(src, "16:9")
    assert out is src  # exact byte-identity check — no re-encode happened


def test_crop_to_aspect_invalid_aspect_returns_input():
    """Garbage aspect string → no-op, never raise."""
    from app.application.image_service import _crop_image_to_aspect

    src = _make_solid_png_bytes(800, 600)
    assert _crop_image_to_aspect(src, "not-an-aspect") is src
    assert _crop_image_to_aspect(src, "0:0") is src
    assert _crop_image_to_aspect(src, "") is src
