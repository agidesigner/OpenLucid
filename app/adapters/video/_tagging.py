"""Cross-provider tag taxonomy for the avatar / voice picker.

Why this module exists: chanjing has a server-side tag dictionary
(/common/tag_list); jogg has none. To keep the picker UX **identical**
across providers — same chip groups, same matching logic — we
synthesize a common set of categories from fields both providers
already expose (gender, age, aspect, language) and merge those into
each provider's `list_*_tags` response. Each item carries a flat
``extras["tag_ids"]: list[str]`` that contains both real-tag tokens
(chanjing's numeric ids stringified) and synthetic tokens; the
frontend filter is then a single algorithm regardless of provider:

    item.extras.tag_ids contains chip.id

Synthetic-token format: ``"<category>:<value>"`` (e.g.
``"gender:female"``, ``"aspect:portrait"``). The colon-prefix keeps
synthetic tokens in their own namespace away from chanjing's numeric
ids (``"10"``, ``"22"``) so the two never collide.
"""

from __future__ import annotations

from app.schemas.media_provider import TagCategory, TagOption


def _cat(cat_id: str, cat_name: str, opts: list[tuple[str, str]]) -> TagCategory:
    return TagCategory(
        id=cat_id, name=cat_name,
        tags=[TagOption(id=oid, name=oname) for oid, oname in opts],
    )


# Categories the picker renders as chips on top of every avatar list.
# Order matters — frontend renders top-to-bottom in this order.
SYNTHETIC_AVATAR_TAG_CATEGORIES: list[TagCategory] = [
    _cat("gender", "性别", [
        ("gender:male", "男"),
        ("gender:female", "女"),
    ]),
    _cat("age", "年龄", [
        ("age:young", "青年"),
        ("age:adult", "中年"),
        ("age:senior", "资深"),
    ]),
    # Framing — chanjing exposes this natively as figures[0].type with the
    # three values below; jogg has no equivalent (its avatars are all
    # whole-body), so jogg-only catalogs hide this category automatically
    # via the "no chip with non-zero count" rule on the frontend.
    _cat("figure", "镜头", [
        ("figure:whole_body", "全身"),
        ("figure:sit_body", "半身"),
        ("figure:circle_view", "头像"),
    ]),
    _cat("aspect", "画幅", [
        ("aspect:portrait", "竖屏"),
        ("aspect:landscape", "横屏"),
        ("aspect:square", "方形"),
    ]),
]

# Voices have no aspect ratio; they do have language. Same structure.
SYNTHETIC_VOICE_TAG_CATEGORIES: list[TagCategory] = [
    _cat("gender", "性别", [
        ("gender:male", "男"),
        ("gender:female", "女"),
    ]),
    _cat("age", "年龄", [
        ("age:young", "青年"),
        ("age:adult", "中年"),
        ("age:senior", "资深"),
    ]),
    _cat("language", "语言", [
        ("language:zh", "中文"),
        ("language:en", "英文"),
    ]),
]


def synthetic_avatar_tag_tokens(
    *,
    gender: str | None,
    age: str | None,
    native_aspect_ratio: str | None,
    figure_type: str | None = None,
) -> list[str]:
    """Produce the synthetic tokens for one avatar from its already-
    normalized fields. Skips fields that are None/unknown so the chip
    filter only matches values we actually know — the alternative
    (emit "gender:unknown") would attach a meaningless token to every
    incomplete record."""
    out: list[str] = []
    if gender in ("male", "female"):
        out.append(f"gender:{gender}")
    if age in ("young", "adult", "senior"):
        out.append(f"age:{age}")
    if figure_type in ("whole_body", "sit_body", "circle_view"):
        out.append(f"figure:{figure_type}")
    if native_aspect_ratio in ("portrait", "landscape", "square"):
        out.append(f"aspect:{native_aspect_ratio}")
    return out


def synthetic_voice_tag_tokens(
    *,
    gender: str | None,
    age: str | None,
    language: str | None,
) -> list[str]:
    out: list[str] = []
    if gender in ("male", "female"):
        out.append(f"gender:{gender}")
    if age in ("young", "adult", "senior"):
        out.append(f"age:{age}")
    if language:
        # Coarse-bucket the locale: zh-CN/zh-TW → zh, en-US/en-GB → en.
        # The chip set covers only major buckets users actually click.
        primary = language.lower().split("-")[0].strip()
        if primary in ("zh", "en"):
            out.append(f"language:{primary}")
    return out
