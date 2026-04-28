"""Pin the subtitle-style contract.

Two things to fence:

1. ``video_service.create_video_job`` must NOT auto-inject brandkit
   primary/secondary as subtitle fill/stroke. Pre-fix, every video
   silently rendered with brand colors regardless of which style the
   user picked — "I selected Classic, why is the subtitle red?" The
   coupling exists in product memory; the regression test makes sure
   nobody re-adds it.

2. ``subtitle_styles.resolve_style`` must return the preset's fill/
   stroke when no override is provided. This is the path that runs
   for every "user picked a preset and didn't customize colors" job.
"""
from __future__ import annotations

import inspect


def test_create_video_job_does_not_inject_brandkit_into_subtitles():
    """The brandkit→subtitle auto-injection was removed because it
    silently overrode every style preset. If someone re-adds it without
    rethinking the explicit-override contract, this test fails.

    The check is source-level (rather than running the full job
    pipeline) so it's resilient to schema/DB changes — the intent is
    to fence a class of regression, not exercise the runtime."""
    from app.application import video_service as svc

    src = inspect.getsource(svc.create_video_job)

    # No brandkit lookup tied to subtitle effective values.
    assert "BrandKitColor" not in src, (
        "BrandKit auto-injection was reintroduced; subtitle colors "
        "must come from the chosen style preset, not the offer's brand."
    )
    # No "by_role[\"primary\"]" pattern — that was the smoking gun.
    assert "by_role" not in src, (
        "Brandkit role→color mapping leaked back into video_service; "
        "if you need per-offer color defaults, do it via an explicit "
        "user-facing toggle, not silent injection."
    )


def test_resolve_style_returns_preset_when_no_override():
    """Smoke test the rendering end of the chain: with style="classic"
    and no overrides, we get white fill + black stroke + 8px width."""
    from app.adapters.video.subtitle_styles import resolve_style

    resolved = resolve_style("classic")
    assert resolved["color"] == "#FFFFFF"
    assert resolved["stroke_color"] == "#000000"
    assert resolved["stroke_width"] == 8


def test_resolve_style_explicit_override_wins():
    """Explicit user override (e.g. via the customize-colors picker)
    always beats the preset. This is the only path through which a
    non-preset color reaches the renderer now."""
    from app.adapters.video.subtitle_styles import resolve_style

    resolved = resolve_style(
        "classic",
        color_override="#FF5733",
        stroke_override="#000000",
    )
    assert resolved["color"] == "#FF5733"
    assert resolved["stroke_color"] == "#000000"
    # Preset's stroke_width is preserved — overrides are color-only.
    assert resolved["stroke_width"] == 8


def test_minimal_preset_uses_high_contrast_pair():
    """The previous ``minimal`` preset paired light gray fill (#E8E8E8)
    with mid-gray stroke (#555) — ~2:1 contrast that vanished against
    most video backgrounds. After redesign it must use a WCAG-AA-clear
    pair (white fill + black stroke), retaining its "subtle" character
    via thinner stroke / smaller font instead of low contrast."""
    from app.adapters.video.subtitle_styles import SUBTITLE_STYLES

    preset = SUBTITLE_STYLES["minimal"]
    assert preset["color"] == "#FFFFFF", (
        "minimal must use pure white fill — earlier #E8E8E8 was "
        "documented as 'understated' but tested as invisible."
    )
    assert preset["stroke_color"] == "#000000", (
        "minimal must use pure black stroke — gray-on-gray failed "
        "WCAG-AA contrast on every real video background."
    )
    # Character should still be "small + thin" — thinner stroke, smaller font.
    assert preset["stroke_width"] < SUBTITLE_STYLES["classic"]["stroke_width"]
    assert preset["size_boost"] < SUBTITLE_STYLES["classic"]["size_boost"]
