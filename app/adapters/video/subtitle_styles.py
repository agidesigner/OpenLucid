"""Shared subtitle-style presets.

The 3 preset styles (classic / bold / minimal) are consumed in two places:

- ``chanjing.py`` — maps them into Chanjing's subtitle_config payload, which
  the provider burns into the avatar portion of the video server-side.
- ``broll_compositor.py`` — maps them into ffmpeg ``drawtext`` params so the
  B-roll cutaway segments match the avatar's subtitle typography.

Keeping the presets here (rather than in ``chanjing.py`` as before) means the
B-roll subtitles and the avatar subtitles are guaranteed to read as the same
style — same color, size, position, stroke weight. Without this shared source
the compositor used its own hardcoded defaults and B-roll subtitles looked
nothing like the avatar's chosen style.
"""
from __future__ import annotations

from typing import Any, Literal

AspectRatio = Literal["portrait", "landscape", "square"]


# Each preset has genuinely different CHARACTER, not just color.
# Fields:
#   color         — fill color (CSS hex)
#   stroke_color  — outline color (CSS hex)
#   stroke_width  — outline thickness in px (tuned for canvas_h ≥ 1080)
#   size_boost    — added to base font size for this style
#   y_ratio       — vertical position as fraction of canvas height
SUBTITLE_STYLES: dict[str, dict[str, Any]] = {
    "classic": {
        "color": "#FFFFFF", "stroke_color": "#000000",
        "stroke_width": 8, "size_boost": 0, "y_ratio": 0.82,
    },
    "bold": {
        # Yellow, larger, thicker outline; positioned a touch higher so the
        # taller text doesn't get cropped in mobile safe-zone.
        "color": "#FFE033", "stroke_color": "#1A1A1A",
        "stroke_width": 10, "size_boost": 6, "y_ratio": 0.78,
    },
    "minimal": {
        # Understated but actually readable. The previous values (light
        # gray text on a mid-gray stroke, ratio ~2:1) failed WCAG-AA
        # contrast against most video backgrounds — users reported it
        # being "invisible". Pure white text + pure black stroke at
        # half the classic stroke width keeps the "low-key" character
        # (smaller font, lower position, thinner outline) without
        # sacrificing legibility on busy footage.
        "color": "#FFFFFF", "stroke_color": "#000000",
        "stroke_width": 4, "size_boost": -2, "y_ratio": 0.86,
    },
}


def _canvas_height(aspect_ratio: AspectRatio) -> int:
    """Height of the canvas the subtitle renders on. Used to pick the right
    base font size — at ≥1920 px tall (portrait) we bump the base so text
    stays readable on a phone screen."""
    if aspect_ratio == "portrait":
        return 1920
    if aspect_ratio == "landscape":
        return 1080
    return 1080  # square


def compute_font_size(aspect_ratio: AspectRatio, style: str) -> int:
    """Base font + style's size_boost. Matches Chanjing's formula so the
    B-roll compositor renders the same visual size as the avatar provider."""
    preset = SUBTITLE_STYLES.get(style) or SUBTITLE_STYLES["classic"]
    base_font = 48 if _canvas_height(aspect_ratio) >= 1920 else 40
    return base_font + preset["size_boost"]


def resolve_style(
    style: str,
    color_override: str | None = None,
    stroke_override: str | None = None,
) -> dict[str, Any]:
    """Return a flat dict with final color / stroke_color / stroke_width /
    size_boost / y_ratio — user overrides applied on top of the preset."""
    preset = SUBTITLE_STYLES.get(style) or SUBTITLE_STYLES["classic"]
    return {
        **preset,
        "color": color_override or preset["color"],
        "stroke_color": stroke_override or preset["stroke_color"],
    }
