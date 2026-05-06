"""Poster template definitions + PIL composite renderer.

MVP ships three templates, hand-derived from the eight reference posters
inside the 蝉镜2.0 offer (3d82ad15) — each captures a recurring shape
across that asset library:

  * digital_human_pitch — digital-human portrait + headline selling-point
  * time_limited_event  — large date/venue chip + activity headline
  * recruit_invite      — invite call + deadline chip + dual portraits

Templates are hardcoded — users select them, they don't author them.
Anti-pattern guard: don't build a model-driven template editor before
anyone has asked for it. Adding a fourth template = ~80 LOC of code,
not a database migration.

Slot positions are stored as ratios (0..1) so the same template renders
consistently at any canvas size. The renderer composites:

    AI background  →  gradient overlays  →  text  →  logo  →  qr
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

logger = logging.getLogger(__name__)

SlotInputType = Literal["text", "qr", "image"]


_CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)


def _resolve_cjk_font(prefer_bold: bool = False) -> str | None:
    """Return the first CJK font path that exists on disk."""
    candidates = _CJK_FONT_CANDIDATES
    if not prefer_bold:
        # Move bold to the back so regular wins when both exist.
        candidates = tuple(c for c in candidates if "Bold" not in c) + tuple(
            c for c in candidates if "Bold" in c
        )
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _resolve_cjk_font(prefer_bold=bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception as e:
            logger.warning("CJK font load failed (%s): %s — falling back to default", path, e)
    return ImageFont.load_default(size=size)


@dataclass(frozen=True)
class Slot:
    key: str
    input_type: SlotInputType
    label: str  # zh-CN — frontend i18n is per-key (see ?lang= API contract)
    label_en: str
    required: bool = True
    max_chars: int | None = None
    placeholder: str | None = None
    # Rendering hints — opaque to UI; only the renderer uses these.
    position: tuple[float, float] = (0.5, 0.5)  # (x_ratio, y_ratio) of slot center
    font_size_ratio: float = 0.05  # font size as fraction of canvas height
    color: str = "#FFFFFF"
    stroke_color: str | None = "#000000"
    stroke_width_ratio: float = 0.004
    align: Literal["left", "center", "right"] = "center"
    width_ratio: float = 0.85  # max width for text wrapping (image slots: target width)
    height_ratio: float = 0.15  # for image/qr slots: target height


@dataclass(frozen=True)
class Template:
    id: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    aspect_ratio: str  # "9:16" | "1:1" | etc.
    canvas_size: tuple[int, int]  # (width, height) in px
    # Used by the legacy generations + PIL fallback path.
    background_prompt_template: str
    # Used by the trust-the-model edits path. Short, behavioral
    # description of HOW elements should sit — the model gets the
    # reference posters as image input, so this stays minimal and avoids
    # restating style cues that the references already carry.
    composition_brief: str = ""
    slots: tuple[Slot, ...] = field(default_factory=tuple)


# ── Template definitions ─────────────────────────────────────────────


_T_DIGITAL_HUMAN_PITCH = Template(
    id="digital_human_pitch",
    name_zh="数字人卖点海报",
    name_en="Digital Human Pitch Poster",
    description_zh="突出数字人形象 + 主标题卖点 + logo + 二维码（参照蝉镜「数字营销助理」海报）",
    description_en="Digital human portrait + headline selling point + logo + QR (modeled after Chanjing's 'Digital Marketing Assistant' poster)",
    aspect_ratio="9:16",
    canvas_size=(1080, 1920),
    background_prompt_template=(
        "Pure photographic background plate for a vertical 9:16 marketing poster. "
        "{style_summary}. "
        "Subject: a single front-facing photorealistic professional adult portrait, "
        "shoulders-up, soft studio lighting, gentle confident expression, occupying the lower-center area. "
        "Surroundings: clean abstract gradient backdrop with soft bokeh, subtle geometric shapes. "
        "The entire upper half is empty negative space — no patterns, no objects. "
        "Render only photographic elements and abstract shapes. "
        "Plain background plate only — leave all chrome (typography, marks, callouts) to be added in post."
    ),
    composition_brief=(
        "Headline at the top third, large and bold. Optional subtitle directly below the headline as a "
        "smaller line or a soft pill. A single front-facing photographic portrait of a professional adult "
        "occupies the center-to-lower area. CTA button (rounded pill) sits between the portrait and the "
        "QR code. Logo top-left. QR code (when provided) bottom-right."
    ),
    slots=(
        Slot(
            key="title",
            input_type="text",
            label="主标题（卖点）",
            label_en="Headline (selling point)",
            required=True,
            max_chars=24,
            placeholder="数字人克隆，让个人IP无限延展",
            position=(0.5, 0.18),
            font_size_ratio=0.062,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width_ratio=0.005,
            align="center",
            width_ratio=0.86,
        ),
        Slot(
            key="subtitle",
            input_type="text",
            label="副标题",
            label_en="Subtitle",
            required=False,
            max_chars=40,
            placeholder="一站式 AI 视频创作平台",
            position=(0.5, 0.30),
            font_size_ratio=0.030,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width_ratio=0.002,
            align="center",
            width_ratio=0.80,
        ),
        Slot(
            key="cta",
            input_type="text",
            label="CTA 按钮文案",
            label_en="CTA pill text",
            required=False,
            max_chars=12,
            placeholder="扫码免费试用",
            position=(0.5, 0.78),
            font_size_ratio=0.028,
            color="#1F2937",
            align="center",
            width_ratio=0.45,
            height_ratio=0.06,
        ),
        Slot(
            key="qr",
            input_type="qr",
            label="二维码",
            label_en="QR code",
            required=True,
            position=(0.82, 0.92),
            width_ratio=0.18,
            height_ratio=0.18,
        ),
    ),
)


_T_TIME_LIMITED_EVENT = Template(
    id="time_limited_event",
    name_zh="限时活动海报",
    name_en="Time-limited Event Poster",
    description_zh="活动主标题 + 日期/地点 chip + 二维码 + logo（参照「AI效率当先广州站」海报）",
    description_en="Event headline + date/venue chip + QR + logo (modeled after Chanjing's 'AI Efficiency Guangzhou' poster)",
    aspect_ratio="9:16",
    canvas_size=(1080, 1920),
    background_prompt_template=(
        "Pure photographic background plate for a vertical 9:16 event poster. "
        "{style_summary}. "
        "Subject: an energetic abstract launch atmosphere — bold geometric color blocks, "
        "soft light streaks, gradient color fields. No people, no objects, no devices. "
        "The entire surface is even-textured and content-free — no panels, no cards, no signage. "
        "Render only photographic and abstract-geometry elements. "
        "Plain background plate only — leave all chrome (typography, marks, callouts) to be added in post."
    ),
    composition_brief=(
        "Bold event headline takes the upper third. A bright contrast date chip sits prominently mid-frame. "
        "Venue line (when provided) directly under the date chip in smaller type. CTA pill near the lower "
        "third. Logo top-left. QR code bottom-right when provided. Energetic, launch-day mood."
    ),
    slots=(
        Slot(
            key="title",
            input_type="text",
            label="活动主标题",
            label_en="Event headline",
            required=True,
            max_chars=20,
            placeholder="AI 效率当先",
            position=(0.5, 0.22),
            font_size_ratio=0.075,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width_ratio=0.005,
            align="center",
            width_ratio=0.88,
        ),
        Slot(
            key="date",
            input_type="text",
            label="日期 / 时间",
            label_en="Date / time",
            required=True,
            max_chars=24,
            placeholder="05.08 14:00 · 周三",
            position=(0.5, 0.42),
            font_size_ratio=0.038,
            color="#FFE033",
            stroke_color="#000000",
            stroke_width_ratio=0.003,
            align="center",
            width_ratio=0.7,
        ),
        Slot(
            key="venue",
            input_type="text",
            label="地点 / 场地",
            label_en="Venue",
            required=False,
            max_chars=30,
            placeholder="广州 · 海珠创意园",
            position=(0.5, 0.50),
            font_size_ratio=0.030,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width_ratio=0.002,
            align="center",
            width_ratio=0.78,
        ),
        Slot(
            key="cta",
            input_type="text",
            label="CTA 按钮",
            label_en="CTA pill",
            required=False,
            max_chars=10,
            placeholder="扫码报名",
            position=(0.5, 0.78),
            font_size_ratio=0.028,
            color="#1F2937",
            align="center",
            width_ratio=0.40,
            height_ratio=0.06,
        ),
        Slot(
            key="qr",
            input_type="qr",
            label="二维码",
            label_en="QR code",
            required=True,
            position=(0.82, 0.92),
            width_ratio=0.18,
            height_ratio=0.18,
        ),
    ),
)


_T_RECRUIT_INVITE = Template(
    id="recruit_invite",
    name_zh="招募邀请海报",
    name_en="Recruit / Invite Poster",
    description_zh="招募邀请文案 + 截止日期 chip + 二维码（参照「寻找数字代言人」海报）",
    description_en="Recruit call + deadline chip + QR (modeled after Chanjing's 'Looking for Digital Spokesperson' poster)",
    aspect_ratio="9:16",
    canvas_size=(1080, 1920),
    background_prompt_template=(
        "Pure photographic background plate for a vertical 9:16 recruitment poster. "
        "{style_summary}. "
        "Subject: two stylized photorealistic adult portraits, three-quarter view, mid-frame, "
        "aspirational lifestyle-photography mood. Soft directional light. "
        "Surroundings: open colored gradient backdrop. "
        "Upper region and lower-center are even-toned negative space — no patterns, no objects. "
        "Render only photographic portraits and gradient backdrop. "
        "Plain background plate only — leave all chrome (typography, marks, callouts) to be added in post."
    ),
    composition_brief=(
        "Recruit headline at the top. Optional subtitle one line below. Two stylized human portraits "
        "anchor the middle. Deadline chip sits centered in the lower third. QR code centered near the "
        "bottom. Logo top-left. Confident, aspirational tone."
    ),
    slots=(
        Slot(
            key="title",
            input_type="text",
            label="招募主标题",
            label_en="Recruit headline",
            required=True,
            max_chars=18,
            placeholder="寻找数字代言人",
            position=(0.5, 0.16),
            font_size_ratio=0.07,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width_ratio=0.005,
            align="center",
            width_ratio=0.86,
        ),
        Slot(
            key="subtitle",
            input_type="text",
            label="副标题",
            label_en="Subtitle",
            required=False,
            max_chars=40,
            placeholder="加入蝉镜，定义你的下一种表达",
            position=(0.5, 0.27),
            font_size_ratio=0.030,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width_ratio=0.002,
            align="center",
            width_ratio=0.80,
        ),
        Slot(
            key="deadline",
            input_type="text",
            label="截止日期",
            label_en="Deadline",
            required=True,
            max_chars=24,
            placeholder="报名截止 5.31",
            position=(0.5, 0.72),
            font_size_ratio=0.034,
            color="#1F2937",
            align="center",
            width_ratio=0.50,
            height_ratio=0.06,
        ),
        Slot(
            key="qr",
            input_type="qr",
            label="二维码",
            label_en="QR code",
            required=True,
            position=(0.50, 0.86),
            width_ratio=0.22,
            height_ratio=0.22,
        ),
    ),
)


TEMPLATES: dict[str, Template] = {
    _T_DIGITAL_HUMAN_PITCH.id: _T_DIGITAL_HUMAN_PITCH,
    _T_TIME_LIMITED_EVENT.id: _T_TIME_LIMITED_EVENT,
    _T_RECRUIT_INVITE.id: _T_RECRUIT_INVITE,
}


def get_template(template_id: str) -> Template | None:
    return TEMPLATES.get(template_id)


def list_templates() -> list[Template]:
    return list(TEMPLATES.values())


# ── Renderer ─────────────────────────────────────────────────────────


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Break ``text`` into lines that fit within ``max_width`` pixels."""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        try:
            bbox = font.getbbox(candidate)
            width = bbox[2] - bbox[0]
        except Exception:
            width = len(candidate) * font.size // 2
        if width > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_text_centered(
    canvas: Image.Image,
    text: str,
    *,
    cx: int,
    cy: int,
    font: ImageFont.FreeTypeFont,
    color: str,
    stroke_color: str | None,
    stroke_width: int,
    max_width: int,
    align: Literal["left", "center", "right"] = "center",
) -> None:
    if not text:
        return
    draw = ImageDraw.Draw(canvas)
    lines = _wrap_lines(text, font, max_width)
    if not lines:
        return
    line_heights = []
    for line in lines:
        bbox = font.getbbox(line)
        line_heights.append(bbox[3] - bbox[1])
    total_h = sum(line_heights) + (len(lines) - 1) * (font.size // 4)
    y = cy - total_h // 2
    for line, lh in zip(lines, line_heights):
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        if align == "center":
            x = cx - line_w // 2
        elif align == "right":
            x = cx + max_width // 2 - line_w
        else:
            x = cx - max_width // 2
        if stroke_color and stroke_width > 0:
            draw.text(
                (x, y),
                line,
                font=font,
                fill=color,
                stroke_width=stroke_width,
                stroke_fill=stroke_color,
            )
        else:
            draw.text((x, y), line, font=font, fill=color)
        y += lh + font.size // 4


def _draw_cta_pill(
    canvas: Image.Image,
    text: str,
    *,
    cx: int,
    cy: int,
    width: int,
    height: int,
    font: ImageFont.FreeTypeFont,
    color: str,
) -> None:
    """Rounded-rectangle CTA button with centered text."""
    if not text:
        return
    draw = ImageDraw.Draw(canvas, "RGBA")
    radius = height // 2
    x0, y0 = cx - width // 2, cy - height // 2
    x1, y1 = cx + width // 2, cy + height // 2
    draw.rounded_rectangle(
        (x0, y0, x1, y1),
        radius=radius,
        fill=(255, 255, 255, 235),
    )
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (cx - tw // 2, cy - th // 2 - bbox[1] // 2),
        text,
        font=font,
        fill=color,
    )


def _luma(rgb: tuple[float, float, float]) -> float:
    """ITU-R BT.601 luma — perceptual brightness for an (R, G, B) tuple."""
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def _sample_bg_luma(canvas: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean luma over a region of the canvas.

    Used to decide whether overlays placed at ``box`` need a light or dark
    treatment for legibility — pure-PIL via ImageStat (no numpy)."""
    region = canvas.crop(box).convert("RGB")
    return _luma(tuple(ImageStat.Stat(region).mean))


def _recolor_dark_text_to_white(image_bytes: bytes) -> bytes:
    """Pure-PIL recolor: turn near-black, low-chroma, opaque pixels to white.

    Used for brandkit logos on dark backgrounds — the wordmark is usually
    drawn in black, which becomes invisible against a dark hero plate.
    Colored elements (orange play-mark, etc.) keep their hue because they
    have high chroma; only the monochrome text channel flips to white.
    """
    src = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    pixels = list(src.getdata())
    out_pixels: list[tuple[int, int, int, int]] = []
    for r, g, b, a in pixels:
        if a > 180:
            chroma = max(r, g, b) - min(r, g, b)
            lum = _luma((r, g, b))
            if lum < 90 and chroma < 60:
                out_pixels.append((255, 255, 255, a))
                continue
        out_pixels.append((r, g, b, a))
    src.putdata(out_pixels)
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    return buf.getvalue()


def _paste_image(
    canvas: Image.Image,
    image_bytes: bytes,
    *,
    cx: int,
    cy: int,
    width: int,
    height: int,
    add_padding: bool = True,
    padding_color: tuple[int, int, int, int] = (255, 255, 255, 240),
) -> None:
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception as e:
        logger.warning("Slot image load failed: %s", e)
        return
    img.thumbnail((width, height), Image.LANCZOS)
    iw, ih = img.size
    if add_padding:
        # White padding tile behind the image so dark backgrounds don't
        # eat low-contrast logos / QR codes.
        pad = max(8, min(iw, ih) // 12)
        tile = Image.new("RGBA", (iw + pad * 2, ih + pad * 2), padding_color)
        tile.paste(img, (pad, pad), img)
        canvas.alpha_composite(tile, (cx - tile.width // 2, cy - tile.height // 2))
    else:
        canvas.alpha_composite(img, (cx - iw // 2, cy - ih // 2))


def _apply_legibility_overlays(canvas: Image.Image) -> None:
    """Soft top + bottom gradients so text stays legible over busy AI backgrounds.

    Top overlay: dark gradient fading down (~30% of canvas height).
    Bottom overlay: dark gradient fading up (~30% of canvas height).
    """
    w, h = canvas.size
    overlay_h_top = int(h * 0.35)
    overlay_h_bot = int(h * 0.30)

    top = Image.new("RGBA", (w, overlay_h_top), (0, 0, 0, 0))
    for y in range(overlay_h_top):
        alpha = int(140 * (1 - y / overlay_h_top))
        ImageDraw.Draw(top).line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
    canvas.alpha_composite(top, (0, 0))

    bot = Image.new("RGBA", (w, overlay_h_bot), (0, 0, 0, 0))
    for y in range(overlay_h_bot):
        alpha = int(110 * (y / overlay_h_bot))
        ImageDraw.Draw(bot).line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
    canvas.alpha_composite(bot, (0, h - overlay_h_bot))


def render_poster(
    template: Template,
    background_bytes: bytes,
    slot_values: dict[str, str | bytes],
    *,
    logo_bytes: bytes | None = None,
) -> bytes:
    """Composite the final poster.

    Inputs:
      * background_bytes — AI-generated bytes (any aspect — will be cropped/resized)
      * slot_values     — dict {slot.key: text_or_bytes}; keys not in template ignored
      * logo_bytes      — optional brandkit logo (rendered top-left at fixed position)

    Returns: PNG bytes.
    """
    cw, ch = template.canvas_size

    # 1. Background — fit-cover crop to canvas.
    try:
        bg = Image.open(io.BytesIO(background_bytes)).convert("RGBA")
    except Exception as e:
        raise ValueError(f"Background image could not be opened: {e}") from e

    bw, bh = bg.size
    canvas_ratio = cw / ch
    bg_ratio = bw / bh
    if bg_ratio > canvas_ratio:
        # bg wider — scale to canvas height, crop sides
        new_h = ch
        new_w = int(bw * (ch / bh))
    else:
        new_w = cw
        new_h = int(bh * (cw / bw))
    bg = bg.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - cw) // 2
    top = (new_h - ch) // 2
    bg = bg.crop((left, top, left + cw, top + ch))
    canvas = bg.convert("RGBA")

    # 2. Legibility overlays.
    _apply_legibility_overlays(canvas)

    # 3. Slots.
    for slot in template.slots:
        cx = int(slot.position[0] * cw)
        cy = int(slot.position[1] * ch)
        if slot.input_type == "text":
            text = str(slot_values.get(slot.key, "") or "").strip()
            if not text and slot.required:
                # Required slot left blank — draw the placeholder so the
                # poster still has shape rather than a hole. The frontend
                # validation should normally prevent this case.
                text = slot.placeholder or ""
            if not text:
                continue
            font_size = max(16, int(slot.font_size_ratio * ch))
            font = _load_font(font_size, bold=True)
            stroke_w = max(1, int(slot.stroke_width_ratio * ch))
            max_w = int(slot.width_ratio * cw)
            if slot.key == "cta":
                pill_w = int(slot.width_ratio * cw)
                pill_h = int(slot.height_ratio * ch)
                _draw_cta_pill(
                    canvas,
                    text,
                    cx=cx,
                    cy=cy,
                    width=pill_w,
                    height=pill_h,
                    font=font,
                    color=slot.color,
                )
            elif slot.key in ("date", "venue", "deadline") and slot.height_ratio:
                # Chip-style: rounded background behind text.
                pill_w = int(slot.width_ratio * cw)
                pill_h = int(slot.height_ratio * ch)
                if pill_h > 0 and slot.key == "deadline":
                    _draw_cta_pill(
                        canvas,
                        text,
                        cx=cx,
                        cy=cy,
                        width=pill_w,
                        height=pill_h,
                        font=font,
                        color=slot.color,
                    )
                else:
                    _draw_text_centered(
                        canvas,
                        text,
                        cx=cx,
                        cy=cy,
                        font=font,
                        color=slot.color,
                        stroke_color=slot.stroke_color,
                        stroke_width=stroke_w,
                        max_width=max_w,
                        align=slot.align,
                    )
            else:
                _draw_text_centered(
                    canvas,
                    text,
                    cx=cx,
                    cy=cy,
                    font=font,
                    color=slot.color,
                    stroke_color=slot.stroke_color,
                    stroke_width=stroke_w,
                    max_width=max_w,
                    align=slot.align,
                )
        elif slot.input_type == "qr":
            qr_bytes = slot_values.get(slot.key)
            if isinstance(qr_bytes, (bytes, bytearray)):
                _paste_image(
                    canvas,
                    bytes(qr_bytes),
                    cx=cx,
                    cy=cy,
                    width=int(slot.width_ratio * cw),
                    height=int(slot.height_ratio * ch),
                    add_padding=True,
                )
        elif slot.input_type == "image":
            img_bytes = slot_values.get(slot.key)
            if isinstance(img_bytes, (bytes, bytearray)):
                _paste_image(
                    canvas,
                    bytes(img_bytes),
                    cx=cx,
                    cy=cy,
                    width=int(slot.width_ratio * cw),
                    height=int(slot.height_ratio * ch),
                    add_padding=False,
                )

    # 4. Logo (top-left, fixed position) — rendered last so it's on top.
    #
    # Contrast-aware recolor: when the AI background under the logo is dark
    # the user's brandkit logo (typically black wordmark on transparent) gets
    # its monochrome channel flipped to white. Without this the wordmark
    # silently disappears into the hero plate; the AI image generator paints
    # whatever luminance fits the photo and we can't dictate it. Sample the
    # canvas BEFORE pasting so the gradient overlay is included in the read.
    if logo_bytes:
        logo_w = int(0.22 * cw)
        logo_h = int(0.10 * ch)
        cx = int(0.18 * cw)
        cy = int(0.06 * ch)
        sample_box = (
            max(0, cx - logo_w // 2),
            max(0, cy - logo_h // 2),
            min(cw, cx + logo_w // 2),
            min(ch, cy + logo_h // 2),
        )
        bg_luma = _sample_bg_luma(canvas, sample_box)
        # Threshold: under ~110 the BT.601 luma reads as "dark plate" — at
        # that level a black wordmark loses ~70% of its visual contrast.
        rendered_logo = (
            _recolor_dark_text_to_white(logo_bytes) if bg_luma < 110 else logo_bytes
        )
        logger.debug(
            "logo paste: bg_luma=%.1f → %s",
            bg_luma,
            "white-text variant" if bg_luma < 110 else "original",
        )
        _paste_image(
            canvas,
            rendered_logo,
            cx=cx,
            cy=cy,
            width=logo_w,
            height=logo_h,
            add_padding=False,
        )

    # 5. Optional subtle blur on the canvas edges to look more polished.
    # (Skipped — keeps render deterministic for fixture testing.)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
