"""Reference-image preparation for image generation."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw


def test_ultra_tall_reference_becomes_readable_contact_sheet():
    from app.application.image_service import _make_long_reference_contact_sheet

    img = Image.new("RGB", (1125, 5902), "white")
    draw = ImageDraw.Draw(img)
    bands = [
        (0, 1900, "#111827", "top hero"),
        (1900, 3900, "#7c3aed", "middle cards"),
        (3900, 5902, "#f97316", "bottom cta"),
    ]
    for y0, y1, color, label in bands:
        draw.rectangle((0, y0, 1125, y1), fill=color)
        draw.text((80, y0 + 120), label, fill="white")

    out = _make_long_reference_contact_sheet(img, max_side=1536)
    assert out is not None

    sheet = Image.open(io.BytesIO(out))
    assert sheet.size[0] == 1536
    assert 760 <= sheet.size[1] <= 900
    # Old longest-side resizing would shrink this source to ~293px wide.
    # The contact sheet keeps each crop around 500px wide, preserving
    # layout/text cues for the image model.
    assert (sheet.size[0] - 24) // 3 >= 500


def test_normal_reference_does_not_use_contact_sheet():
    from app.application.image_service import _make_long_reference_contact_sheet

    img = Image.new("RGB", (1200, 1600), "white")
    assert _make_long_reference_contact_sheet(img, max_side=1536) is None
