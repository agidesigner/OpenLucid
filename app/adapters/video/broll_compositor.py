"""B-roll compositor — inserts AI-directed cutaway clips into an avatar video.

The avatar's audio track runs CONTINUOUSLY and uncut.
Only the VISUAL track switches briefly to B-roll at calculated timestamps.

Insert timestamps are derived from `insert_after_char` in the broll_plan:
  - Total narration text length maps to total video duration
  - Each char position maps to a proportional timestamp
  - This gives content-aware insertion (B-roll appears when the speaker
    mentions the relevant topic, not at arbitrary mechanical positions)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

logger = logging.getLogger(__name__)


async def _download(url: str, dest: Path) -> None:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(url, timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)
    logger.info("Downloaded %s -> %s (%.1f MB)", url[:80], dest.name, dest.stat().st_size / 1e6)


_FFMPEG_MISSING_HINT = (
    "ffmpeg/ffprobe not found on PATH. B-roll compositing requires the ffmpeg "
    "binary. Install it and restart the app — Docker images bundle it already; "
    "on bare Windows run `winget install Gyan.FFmpeg` (then reopen the shell); "
    "on macOS `brew install ffmpeg`; on Debian/Ubuntu `apt-get install ffmpeg`."
)


async def _ffprobe_duration(path: Path) -> float:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise RuntimeError(_FFMPEG_MISSING_HINT) from e
    stdout, _ = await proc.communicate()
    return float(stdout.strip() or 0)


async def _ffmpeg(*args: str) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "warning", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise RuntimeError(_FFMPEG_MISSING_HINT) from e
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (rc={proc.returncode}): {stderr.decode()[:500]}")


def _char_to_timestamp(
    insert_after_char: int, total_chars: int, video_duration: float
) -> float:
    """Convert a character position in narration text to a video timestamp.

    Assumes roughly constant speaking rate (chars / second).
    """
    if total_chars <= 0:
        return 0.0
    ratio = max(0.0, min(1.0, insert_after_char / total_chars))
    return ratio * video_duration


def _extract_subtitle_chunks(
    insert_after_char: int, duration_seconds: float,
    total_text: str, total_duration: float,
) -> list[tuple[str, float, float]]:
    """Slice the narration window that plays during a B-roll segment into
    phrase-level chunks timed to the avatar's speech rhythm.

    Previously this returned a single string and the caller rendered it as
    one static block across the whole B-roll segment — 30 Chinese chars on
    one line blew past the 1080-px frame width. Now we split on CJK / Latin
    clause punctuation and hand back ``[(text, start_sec, end_sec), ...]``
    so the caller can emit one ``drawtext`` per chunk with its own ``enable``
    window. Result: subtitles refresh every ~1-2 s in sync with narration,
    same read cadence as the avatar provider's burn-in.
    """
    if not total_text or total_duration <= 0 or duration_seconds <= 0:
        return []
    import re
    chars_per_sec = len(total_text) / total_duration
    start_char = int(insert_after_char)
    end_char = int(start_char + duration_seconds * chars_per_sec)
    # Snap ``end_char`` to the nearest word/clause boundary (space or
    # CJK/Latin punctuation) so we don't slice a word in half. Without
    # this a 6-s window that lands mid-"neglected" shows "…neglecte".
    # Look both directions up to 12 chars (longest common English word)
    # and pick whichever is closer; on ties, prefer the forward one so
    # the clip shows slightly MORE content rather than less.
    BOUNDARY_CHARS = " ，。！？；,;.!?\n\t"
    SEARCH_RADIUS = 12
    back_hit = None
    fwd_hit = None
    for offset in range(1, SEARCH_RADIUS + 1):
        if back_hit is None and end_char - offset > start_char:
            i = end_char - offset
            if 0 <= i < len(total_text) and total_text[i] in BOUNDARY_CHARS:
                back_hit = i
        if fwd_hit is None:
            i = end_char + offset
            if i < len(total_text) and total_text[i] in BOUNDARY_CHARS:
                fwd_hit = i
        if back_hit is not None and fwd_hit is not None:
            break
    if back_hit is not None and fwd_hit is not None:
        end_char = fwd_hit if (fwd_hit - end_char) <= (end_char - back_hit) else back_hit
    elif fwd_hit is not None:
        end_char = fwd_hit
    elif back_hit is not None:
        end_char = back_hit
    # else: no boundary found within radius — accept the hard cut.
    window_text = total_text[start_char:end_char].strip()
    if not window_text:
        return []
    # Split on CJK + Latin clause-level punctuation. Keep non-empty parts.
    raw = re.split(r"[，。！？；,;.!?\n]+", window_text)
    phrases = [p.strip() for p in raw if p.strip()]
    if not phrases:
        phrases = [window_text]

    # Further split any phrase that overflows the frame. Limits are
    # language-aware: CJK chars are ~2x wider than Latin at the same
    # font size, so we cap Chinese at ~14 chars and English at ~32 chars
    # (roughly 6-7 English words) to keep both under the 1080-px portrait
    # width when centered. For English we split on word boundaries, never
    # mid-word — a previous version used a char-count slice that cut
    # "thousands" into "thousa" + "nds" on screen.
    CJK_LIMIT = 14
    LATIN_LIMIT = 32

    def _is_cjk(text: str) -> bool:
        # 30 %+ CJK characters is "mostly Chinese" for our purposes.
        cjk = sum(1 for c in text if "一" <= c <= "鿿")
        nonspace = sum(1 for c in text if not c.isspace())
        return nonspace > 0 and (cjk / nonspace) >= 0.3

    def _split_overlong(p: str) -> list[str]:
        if _is_cjk(p):
            if len(p) <= CJK_LIMIT:
                return [p]
            return [p[i : i + CJK_LIMIT] for i in range(0, len(p), CJK_LIMIT)]
        # Latin / mixed path — word-boundary packing so we never break a word.
        if len(p) <= LATIN_LIMIT:
            return [p]
        words = p.split()
        out: list[str] = []
        buf = ""
        for w in words:
            # +1 for the space between buf and w
            candidate_len = len(buf) + (1 if buf else 0) + len(w)
            if buf and candidate_len > LATIN_LIMIT:
                out.append(buf)
                buf = w
            else:
                buf = f"{buf} {w}" if buf else w
        if buf:
            out.append(buf)
        return out

    expanded: list[str] = []
    for p in phrases:
        expanded.extend(_split_overlong(p))
    phrases = expanded

    # Allocate on-screen time per phrase in proportion to its char count.
    total_chars = sum(len(p) for p in phrases)
    if total_chars == 0:
        return []
    # Enforce a minimum per-chunk dwell so viewers can actually read
    # short phrases ("I did") — sub-0.8s flashes are unreadable. If a
    # phrase's proportional share is below the floor, pad it and shave
    # proportionally from oversized neighbors at final distribution.
    MIN_DWELL = 0.8

    raw_durs = [duration_seconds * (len(p) / total_chars) for p in phrases]
    # Two-pass: promote under-floor shares to MIN_DWELL, then shrink the
    # over-floor shares proportionally to stay within duration_seconds.
    final_durs: list[float] = list(raw_durs)
    under = [i for i, d in enumerate(final_durs) if d < MIN_DWELL]
    if under and len(phrases) > 1:
        shortfall = sum(MIN_DWELL - final_durs[i] for i in under)
        over_idx = [i for i, d in enumerate(final_durs) if d >= MIN_DWELL]
        over_sum = sum(final_durs[i] for i in over_idx)
        if over_sum > shortfall + len(over_idx) * MIN_DWELL:
            # Safe to rebalance without pushing overs below MIN_DWELL.
            for i in under:
                final_durs[i] = MIN_DWELL
            scale = (duration_seconds - len(under) * MIN_DWELL) / over_sum if over_sum > 0 else 1.0
            for i in over_idx:
                final_durs[i] *= scale
        # else: the clip is too short for that many chunks — accept the
        # raw proportional split and let the short ones flash (better
        # than starving long chunks).

    chunks: list[tuple[str, float, float]] = []
    cursor = 0.0
    for p, d in zip(phrases, final_durs):
        chunks.append((p, cursor, cursor + d))
        cursor += d
    return chunks


def _resolve_cjk_font_file() -> str | None:
    """Find an installed CJK font for ffmpeg drawtext.

    The drawtext filter's ``font=<family>`` path relies on fontconfig and
    silently falls back to a Latin-only built-in when the family can't be
    resolved, which renders Chinese as tofu. Passing ``fontfile=<path>``
    skips fontconfig and is the only reliable way to guarantee rendering
    across deployments. Probes the standard Debian install paths in order
    of preference (Noto CJK → WQY fallbacks) and returns the first hit.
    """
    import os
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


_CJK_FONT_FILE: str | None = None


def _ffmpeg_color(hex_or_name: str) -> str:
    """Turn a CSS-style hex (``#FFE033``) into the ``0xRRGGBB`` form ffmpeg's
    drawtext likes best. ``#`` is technically accepted but some ffmpeg builds
    misparse it; ``0xRRGGBB`` is universally safe. Passes through named colors
    and already-normalized forms unchanged.
    """
    s = (hex_or_name or "").strip()
    if s.startswith("#") and len(s) in (4, 7, 9):
        return "0x" + s[1:]
    return s or "white"


def _drawtext_filter(
    text: str,
    *,
    font_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 8,
    font_size: int = 40,
    y_ratio: float = 0.82,
    enable: str | None = None,
) -> str:
    """Build FFmpeg drawtext filter for subtitle overlay on a B-roll segment.

    Takes the resolved style dict from ``subtitle_styles.resolve_style`` so the
    compositor output matches the avatar provider's burned-in subtitles —
    same color, outline weight, size boost, and vertical position.

    ``enable`` accepts an ffmpeg expression (e.g. ``between(t,0,1.5)``) to
    limit when the text is visible. When multiple chunks are chained in the
    same filtergraph with non-overlapping windows, the subtitle appears to
    refresh in sync with the narration.
    """
    if not text:
        return "null"
    global _CJK_FONT_FILE
    if _CJK_FONT_FILE is None:
        _CJK_FONT_FILE = _resolve_cjk_font_file() or ""
        if not _CJK_FONT_FILE:
            logger.warning(
                "B-roll subtitle: no CJK font installed — Chinese subtitles "
                "will render as tofu. Install fonts-noto-cjk in the image."
            )
    # Chanjing's ``stroke_width`` is tuned for a 1920-tall portrait canvas
    # rendered by a burn-in compositor. ffmpeg's ``borderw`` in drawtext
    # reads pixels directly, same meaning — so no scaling needed. But the
    # preset numbers (8/10/4) are slightly too heavy for ffmpeg's renderer
    # at the same canvas, producing chunky outlines. Halve them for parity.
    border = max(2, stroke_width // 2)
    # Escape special chars for FFmpeg drawtext
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "%%")
    font_opt = (
        f":fontfile={_CJK_FONT_FILE}"
        if _CJK_FONT_FILE else ":font=Noto Sans CJK SC"
    )
    enable_opt = f":enable='{enable}'" if enable else ""
    return (
        f"drawtext=text='{escaped}'"
        + enable_opt
        + f":fontsize={font_size}"
        + f":fontcolor={_ffmpeg_color(font_color)}"
        + f":borderw={border}"
        + f":bordercolor={_ffmpeg_color(stroke_color)}"
        + ":x=(w-text_w)/2"
        + f":y=h*{y_ratio}"
        + font_opt
    )


async def composite_broll(
    avatar_video_url: str,
    broll_clips: list[dict],
    section_order: list[str],
    sections: dict,
    output_dir: str,
    *,
    caption: bool = True,
    aspect_ratio: str = "portrait",
    subtitle_style: str = "classic",
    subtitle_color: str | None = None,
    subtitle_stroke: str | None = None,
) -> str:
    """Insert B-roll cutaways into avatar video at AI-directed timestamps.

    Args:
        avatar_video_url: URL of the full avatar talking-head video.
        broll_clips: [{url, insert_after_char, duration_seconds, type, prompt}, ...]
        section_order: Ordered section IDs.
        sections: {id: {text, duration_seconds, ...}} from structured_content.
        output_dir: Where to write the final video.
        caption: When False, skip subtitle overlay entirely (matches the
            avatar provider, which respects the same flag server-side).
        aspect_ratio: ``portrait`` | ``landscape`` | ``square`` — drives the
            subtitle font base size so the overlay matches the avatar's.
        subtitle_style / subtitle_color / subtitle_stroke: Same style system
            used by the avatar provider; shared via ``subtitle_styles``.

    Returns:
        Local file path of the composited video.
    """
    from app.adapters.video.subtitle_styles import compute_font_size, resolve_style

    style_params = resolve_style(subtitle_style, subtitle_color, subtitle_stroke)
    font_size = compute_font_size(aspect_ratio, subtitle_style)

    # Target output resolution — must match the avatar's aspect so ffmpeg
    # concat doesn't reject mismatched dims. Previously hardcoded to
    # portrait 1080x1920, which silently stretched/cropped landscape and
    # square avatars when users picked those aspects.
    _DIMS_BY_ASPECT = {
        "portrait": (1080, 1920),
        "landscape": (1920, 1080),
        "square": (1080, 1080),
    }
    out_w, out_h = _DIMS_BY_ASPECT.get(aspect_ratio, (1080, 1920))
    with TemporaryDirectory(prefix="broll_") as tmp:
        tmp_path = Path(tmp)

        # 1. Download avatar video
        avatar_path = tmp_path / "avatar.mp4"
        await _download(avatar_video_url, avatar_path)
        avatar_duration = await _ffprobe_duration(avatar_path)
        logger.info("Avatar video: %.1fs", avatar_duration)

        # 2. Calculate total narration length for char→timestamp mapping
        order = section_order or list(sections.keys())
        total_text = ""
        for sid in order:
            sec = sections.get(sid, {})
            total_text += (sec.get("text") or "")
        total_chars = len(total_text)
        logger.info("Narration: %d chars, video: %.1fs (≈%.1f chars/s)",
                     total_chars, avatar_duration,
                     total_chars / avatar_duration if avatar_duration else 0)

        # 3. Download B-roll clips and calculate insert timestamps
        insert_points: list[tuple[float, Path, float]] = []  # (timestamp, path, duration)
        for i, clip in enumerate(broll_clips):
            url = clip.get("url")
            if not url:
                continue
            p = tmp_path / f"broll_{i}.mp4"
            await _download(url, p)

            char_pos = clip.get("insert_after_char", 0)
            if isinstance(char_pos, str):
                try:
                    char_pos = int(char_pos)
                except (ValueError, TypeError):
                    char_pos = 0
            timestamp = _char_to_timestamp(char_pos, total_chars, avatar_duration)
            # Align the compositor cap with the composer spec (5-10s).
            # Previously this was capped at 6 which silently truncated any
            # 8-10s "slow-motion reveal" clips the LLM intentionally planned.
            clip_dur = max(3.0, min(clip.get("duration_seconds") or 5, 10.0))
            insert_points.append((timestamp, p, clip_dur))

        if not insert_points:
            logger.warning("No B-roll clips to insert, returning avatar as-is")
            output_name = f"broll_{uuid.uuid4().hex[:12]}.mp4"
            output_path = Path(output_dir) / output_name
            output_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(avatar_path, output_path)
            return str(output_path)

        # Sort by timestamp and ensure no overlaps
        insert_points.sort(key=lambda x: x[0])
        logger.info("Insert plan: %s", [(f"{t:.1f}s", p.name, f"{d:.0f}s") for t, p, d in insert_points])

        # 4. Extract audio from avatar (continuous, uncut)
        audio_path = tmp_path / "audio.aac"
        await _ffmpeg("-i", str(avatar_path), "-vn", "-acodec", "aac", "-b:a", "128k", str(audio_path))

        # 5. Build visual segments: avatar → B-roll → avatar → B-roll → ...
        #    For avatar segments > MAX_UNCUT seconds, auto-insert zoom changes
        #    to simulate camera angle switches (keeps visual rhythm alive).
        MAX_UNCUT = 12.0  # seconds — max time on one static avatar shot
        ZOOM_LEVELS = [1.0, 1.15, 1.0, 1.2, 1.0, 1.12]  # cycle through

        segments: list[Path] = []
        zoom_idx = 0  # track which zoom level to use next
        cursor = 0.0

        async def _add_avatar_segment(start: float, end: float):
            """Add avatar segment(s), auto-splitting with zoom if too long."""
            nonlocal zoom_idx
            dur = end - start
            if dur < 1.0:
                return

            if dur <= MAX_UNCUT:
                # Short enough — single segment with current zoom
                zoom = ZOOM_LEVELS[zoom_idx % len(ZOOM_LEVELS)]
                zoom_idx += 1
                seg = tmp_path / f"seg_av_{len(segments)}.mp4"
                if zoom == 1.0:
                    vf = "null"  # no filter needed
                else:
                    # Crop center at zoom level, then scale back to original resolution
                    cw = f"iw/{zoom:.2f}"
                    ch = f"ih/{zoom:.2f}"
                    vf = f"crop={cw}:{ch}:(iw-{cw})/2:(ih-{ch})/2,scale={out_w}:{out_h}:flags=lanczos,setsar=1"
                await _ffmpeg(
                    "-i", str(avatar_path),
                    "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
                    "-vf", vf,
                    "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    str(seg),
                )
                segments.append(seg)
            else:
                # Too long — split into chunks of ~10s with alternating zoom
                chunk_dur = min(10.0, dur / 2)
                t = start
                while t < end - 1.0:
                    chunk_end = min(t + chunk_dur, end)
                    await _add_avatar_segment(t, chunk_end)
                    t = chunk_end

        for insert_time, clip_path, clip_dur in insert_points:
            # Retention-opener exception: the first clip at t≈0 is a hook
            # that REPLACES the first seconds of avatar, not an inter-cut.
            # Without this, the "insert_time < cursor + 1.0" guard below
            # drops every retention shot (cursor also starts at 0), leaving
            # 15+ seconds of static avatar before the first B-roll appears.
            is_retention_opener = (cursor == 0.0 and insert_time <= 0.5)

            # Clamp: don't insert before cursor (1s buffer to avoid
            # adjacent B-rolls pressing together) or in last 3 seconds.
            # The opener bypasses the cursor-proximity check.
            too_close = (not is_retention_opener) and insert_time < cursor + 1.0
            if too_close or insert_time > avatar_duration - 3.0:
                logger.info("Skipping B-roll at %.1fs (cursor=%.1f, end=%.1f)", insert_time, cursor, avatar_duration)
                continue

            # Avatar segment(s) before this B-roll insert (none for the
            # opener since it starts at 0 — the audio remains continuous
            # via the separately-extracted master track merged at the end).
            if insert_time > cursor + 0.5:
                await _add_avatar_segment(cursor, insert_time)

            # B-roll insert (scale to match avatar resolution + burn subtitle)
            actual_dur = await _ffprobe_duration(clip_path)
            use_dur = min(actual_dur, clip_dur)
            seg = tmp_path / f"seg_br_{len(segments)}.mp4"
            # Find the narration text for this B-roll segment
            char_pos = 0
            for _t, _p, _d in insert_points:
                if _p == clip_path:
                    # Find original insert_after_char from broll_clips
                    for bc in broll_clips:
                        if bc.get("insert_after_char") is not None:
                            pos = bc["insert_after_char"]
                            if isinstance(pos, str):
                                try: pos = int(pos)
                                except: pos = 0
                            if abs(_char_to_timestamp(pos, total_chars, avatar_duration) - insert_time) < 2:
                                char_pos = pos
                                break
                    break
            subtitle_chunks = (
                _extract_subtitle_chunks(char_pos, use_dur, total_text, avatar_duration)
                if caption else []
            )
            scale_filter = f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:-1:-1:color=black,setsar=1"
            # Chain one ``drawtext`` per phrase chunk; ``enable=between(t,a,b)``
            # makes each appear only during its window. Multiple drawtext
            # filters layered this way behave like the avatar's per-clause
            # subtitle refresh — users see ~8 chars at a time, in sync with
            # what's being narrated, instead of a 30-char wall of text.
            chunk_filters = [
                _drawtext_filter(
                    chunk_text,
                    font_color=style_params["color"],
                    stroke_color=style_params["stroke_color"],
                    stroke_width=style_params["stroke_width"],
                    font_size=font_size,
                    y_ratio=style_params["y_ratio"],
                    enable=f"between(t,{s:.2f},{e:.2f})",
                )
                for (chunk_text, s, e) in subtitle_chunks
            ]
            vf = ",".join([scale_filter, *chunk_filters]) if chunk_filters else scale_filter
            await _ffmpeg(
                "-i", str(clip_path),
                "-t", f"{use_dur:.2f}",
                "-vf", vf,
                "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                str(seg),
            )
            segments.append(seg)
            cursor = insert_time + use_dur
            zoom_idx += 1  # reset zoom cycle after B-roll

        # Final avatar segment(s)
        if cursor < avatar_duration - 0.5:
            await _add_avatar_segment(cursor, avatar_duration)

        # 6. Concatenate + merge with audio
        concat_list = tmp_path / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p.name}'" for p in segments))
        visual_path = tmp_path / "visual.mp4"
        await _ffmpeg(
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            str(visual_path),
        )

        output_name = f"broll_{uuid.uuid4().hex[:12]}.mp4"
        output_path = Path(output_dir) / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await _ffmpeg(
            "-i", str(visual_path), "-i", str(audio_path),
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(output_path),
        )

        final_dur = await _ffprobe_duration(output_path)
        broll_total = sum(d for _, _, d in insert_points)
        logger.info(
            "Composite done: %s (%.1fs, B-roll ~%.1fs ≈%.0f%%)",
            output_path.name, final_dur, broll_total,
            broll_total / final_dur * 100 if final_dur else 0,
        )
        return str(output_path)
