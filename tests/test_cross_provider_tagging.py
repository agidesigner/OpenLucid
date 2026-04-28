"""Pin the cross-provider tagging abstraction.

Why this file exists: the picker MUST present and behave identically
across chanjing and jogg. The mechanism is a unified ``extras["tag_ids"]``
contract:

  * Both providers emit a flat ``list[str]`` of token strings.
  * Synthetic tokens follow ``"<category>:<value>"`` (e.g.
    ``"gender:female"``, ``"aspect:portrait"``).
  * chanjing ALSO carries its real tag-dictionary ids stringified
    (``"10"``, ``"22"``).
  * One frontend filter algorithm — ``tag_ids contains chip.id`` — works
    for either provider with no branching.

These tests fence the contract so a refactor can't quietly break the
"two providers feel identical" promise.
"""
from __future__ import annotations

import asyncio


# ── Synthetic-token helper unit tests ───────────────────────────────


def test_synthetic_avatar_tokens_skip_unknown_values():
    """Unknown / None values do NOT become 'gender:unknown' tokens —
    silently attaching meaningless tokens would let chips falsely match
    incomplete records."""
    from app.adapters.video._tagging import synthetic_avatar_tag_tokens

    assert synthetic_avatar_tag_tokens(
        gender="female", age="young", native_aspect_ratio="portrait",
    ) == ["gender:female", "age:young", "aspect:portrait"]

    assert synthetic_avatar_tag_tokens(
        gender=None, age="adult", native_aspect_ratio=None,
    ) == ["age:adult"]

    # Unknown enum values are dropped (provider returns garbage → still safe).
    assert synthetic_avatar_tag_tokens(
        gender="other", age="middle_aged", native_aspect_ratio="weird",
    ) == []


def test_synthetic_avatar_tokens_include_figure_type():
    """figure_type (chanjing's whole_body / sit_body / circle_view)
    is the third synthetic chip dimension. jogg has no equivalent and
    passes None → token absent. The chip filter then auto-hides the
    figure chip group on jogg-only catalogs (no chip with non-zero
    count), so the abstraction stays clean."""
    from app.adapters.video._tagging import synthetic_avatar_tag_tokens

    # chanjing-shaped input
    assert synthetic_avatar_tag_tokens(
        gender="female", age="adult",
        native_aspect_ratio="portrait", figure_type="sit_body",
    ) == ["gender:female", "age:adult", "figure:sit_body", "aspect:portrait"]

    # jogg-shaped input — no figure
    assert synthetic_avatar_tag_tokens(
        gender="female", age="adult",
        native_aspect_ratio="portrait",
    ) == ["gender:female", "age:adult", "aspect:portrait"]

    # Unknown figure value → drop (don't emit "figure:other")
    assert synthetic_avatar_tag_tokens(
        gender=None, age=None, native_aspect_ratio=None,
        figure_type="weird_pose",
    ) == []


def test_synthetic_voice_tokens_normalize_locale():
    """Locale tokens collapse to coarse buckets (zh-CN/zh-TW → zh) so
    chanjing's "zh-CN" voices and jogg's "en" voices both match the
    'language' chip set without fragmenting it across regional codes."""
    from app.adapters.video._tagging import synthetic_voice_tag_tokens

    assert synthetic_voice_tag_tokens(
        gender="male", age="adult", language="zh-CN",
    ) == ["gender:male", "age:adult", "language:zh"]

    assert synthetic_voice_tag_tokens(
        gender="female", age=None, language="en-US",
    ) == ["gender:female", "language:en"]

    # Unsupported locale → drop the language token rather than emit a
    # chip nobody can ever click. Frontend extends the chip set when a
    # provider adds new locales.
    assert synthetic_voice_tag_tokens(
        gender=None, age=None, language="ja-JP",
    ) == []


def test_synthetic_categories_are_stable_id_format():
    """Category ids are short stable strings ('gender', 'age', ...);
    chip ids inside use the colon-namespaced format. Frontend code
    keys off these literals — pin them so a typo in the constants
    breaks tests, not the live picker."""
    from app.adapters.video._tagging import (
        SYNTHETIC_AVATAR_TAG_CATEGORIES,
        SYNTHETIC_VOICE_TAG_CATEGORIES,
    )

    avatar_cats = {c.id for c in SYNTHETIC_AVATAR_TAG_CATEGORIES}
    assert avatar_cats == {"gender", "age", "figure", "aspect"}

    voice_cats = {c.id for c in SYNTHETIC_VOICE_TAG_CATEGORIES}
    assert voice_cats == {"gender", "age", "language"}

    # Every chip id MUST contain a colon (synthetic-token namespace
    # boundary). Without this, jogg's "gender:female" would collide
    # with a hypothetical chanjing tag that happens to be id=42 and
    # named "female".
    for cat in SYNTHETIC_AVATAR_TAG_CATEGORIES + SYNTHETIC_VOICE_TAG_CATEGORIES:
        for tag in cat.tags:
            assert ":" in tag.id, f"chip id {tag.id!r} missing namespace prefix"


# ── Provider parity ─────────────────────────────────────────────────


def test_jogg_avatar_emits_same_extras_keys_as_chanjing():
    """Both providers must emit ``native_aspect_ratio`` and ``tag_ids``
    extras keys for an avatar with comparable raw fields. If jogg ever
    drifted (e.g. back to extras["aspect_ratio"]), the picker would
    have to branch on provider — defeating the abstraction."""
    from app.adapters.video.jogg import JoggVideoProvider
    from app.adapters.video.chanjing import ChanjingVideoProvider

    # Equivalent inputs across the two providers' wire formats.
    jogg_avatar = JoggVideoProvider._parse_avatar_item({
        "id": 42, "name": "x", "gender": "female", "age": "young_adult",
        "cover_url": "https://x/c.png", "video_url": "https://x/v.mp4",
        "aspect_ratio": 0,  # jogg's portrait code
    })
    chanjing_avatar = ChanjingVideoProvider._parse_avatar_item({
        "id": "ch-42", "name": "x", "gender": "female",
        "figures": [{"type": "whole_body", "cover": "https://x/c.png"}],
        "tag_names": ["青年"],  # chanjing's path to age=young
        "width": 1080, "height": 1920,
    })

    # Same keys exist on both.
    assert "native_aspect_ratio" in jogg_avatar.extras
    assert "native_aspect_ratio" in chanjing_avatar.extras
    assert "tag_ids" in jogg_avatar.extras
    assert "tag_ids" in chanjing_avatar.extras

    # Same enum value for the same logical aspect.
    assert jogg_avatar.extras["native_aspect_ratio"] == "portrait"
    assert chanjing_avatar.extras["native_aspect_ratio"] == "portrait"

    # Both contain the same synthetic tokens (chanjing also has its
    # real ids prepended, but that's a chanjing-specific superset).
    expected_synthetic = {"gender:female", "age:young", "aspect:portrait"}
    assert expected_synthetic.issubset(jogg_avatar.extras["tag_ids"])
    assert expected_synthetic.issubset(chanjing_avatar.extras["tag_ids"])


def test_jogg_voice_carries_tag_ids_for_chip_filter():
    """Voice extras must mirror avatar extras — chip filter is the same
    matching algorithm for both lists."""
    from app.adapters.video.jogg import JoggVideoProvider

    voice = JoggVideoProvider._parse_voice_item({
        "voice_id": "v-1", "name": "x", "gender": "female",
        "language": "en", "audio_url": "https://x/a.mp3", "age": "young",
    })
    assert voice is not None
    assert voice.extras["tag_ids"] == [
        "gender:female", "age:young", "language:en",
    ]


def test_chanjing_voice_carries_tag_ids_for_chip_filter():
    """Same contract on chanjing voices — synthesized from the
    voice-name age heuristic + lang field."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    voice = ChanjingVideoProvider._parse_voice_item({
        "id": "cv-1", "name": "林小妹", "gender": "female",
        "lang": "zh-CN", "audition": "https://x/a.mp3",
    })
    assert voice is not None
    assert voice.extras["tag_ids"] == [
        "gender:female", "age:young", "language:zh",
    ]


def test_jogg_list_avatar_tags_returns_synthetic_categories():
    """jogg has no upstream tag_list; the picker still sees the same
    chip groups as chanjing (gender/age/aspect) because we always emit
    the synthetic categories. Without this, jogg picker would render
    an empty filter bar — UX inconsistency."""
    from app.adapters.video.jogg import JoggVideoProvider

    p = JoggVideoProvider.__new__(JoggVideoProvider)
    p._api_key = "x"
    p._base_url = "https://api.jogg.ai/v2"

    cats = asyncio.run(p.list_avatar_tags())
    cat_ids = {c["id"] for c in cats}
    # jogg avatars don't carry figure_type tokens, so the figure
    # category will be hidden by the empty-chip rule on the frontend —
    # but the dictionary still includes it for cross-provider parity.
    assert cat_ids == {"gender", "age", "figure", "aspect"}

    voice_cats = asyncio.run(p.list_voice_tags())
    voice_cat_ids = {c["id"] for c in voice_cats}
    assert voice_cat_ids == {"gender", "age", "language"}


def test_chanjing_list_avatar_tags_appends_synthetic_to_real():
    """Chanjing serves real tag categories AND must append the synthetic
    categories so the same chip set is available regardless of whether
    chanjing's admins have populated their dictionary."""
    from app.adapters.video.chanjing import ChanjingVideoProvider

    p = ChanjingVideoProvider.__new__(ChanjingVideoProvider)
    p.api_key = "x"

    async def _fake_request(self, method, path, params=None, json_body=None, **kw):
        # Pretend chanjing returns one real category.
        return {
            "code": 0,
            "data": {
                "list": [{
                    "id": 1, "name": "场景",
                    "tag_list": [{"id": 10, "name": "商务", "parent_id": 0}],
                }],
            },
        }

    type(p)._request = _fake_request  # type: ignore[assignment]

    cats = asyncio.run(p.list_avatar_tags())
    cat_ids = [c["id"] for c in cats]
    # Real category first, then synthetic — order matters for chip rendering.
    assert cat_ids == ["1", "gender", "age", "figure", "aspect"]


def test_voiceitem_schema_has_extras_for_picker_parity():
    """VoiceItem must carry extras so list_voices_for_config can
    propagate tag_ids to the frontend — without this field, the API
    response would silently drop voice tag_ids and break voice chip
    filters."""
    from app.schemas.media_provider import VoiceItem

    item = VoiceItem(
        id="v", name="x", sample_url="https://x/s.mp3",
        extras={"tag_ids": ["gender:female"]},
    )
    assert item.extras == {"tag_ids": ["gender:female"]}
    # Default empty dict for backward compat.
    bare = VoiceItem(id="v", name="x", sample_url="https://x/s.mp3")
    assert bare.extras == {}
