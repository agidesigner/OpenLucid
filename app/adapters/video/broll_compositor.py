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


def _extract_subtitle_text(
    insert_after_char: int, duration_seconds: float,
    total_text: str, total_duration: float,
) -> str:
    """Extract the narration text that plays during a B-roll segment.

    Uses char position → time mapping to find which part of the narration
    corresponds to this B-roll clip, then returns that text for subtitle overlay.
    """
    if not total_text or total_duration <= 0:
        return ""
    chars_per_sec = len(total_text) / total_duration
    start_char = int(insert_after_char)
    end_char = int(start_char + duration_seconds * chars_per_sec)
    text = total_text[start_char:end_char].strip()
    # Limit to ~30 chars for readability on screen
    if len(text) > 30:
        text = text[:28] + "…"
    return text


def _drawtext_filter(text: str, fontsize: int = 36) -> str:
    """Build FFmpeg drawtext filter for subtitle overlay on B-roll segments.

    White text with black outline at bottom 82%, matching the Chanjing subtitle style.
    """
    if not text:
        return "null"
    # Escape special chars for FFmpeg drawtext
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "%%")
    return (
        f"drawtext=text='{escaped}'"
        f":fontsize={fontsize}"
        ":fontcolor=white"
        ":borderw=3"
        ":bordercolor=black"
        ":x=(w-text_w)/2"
        ":y=h*0.82"
        ":font=Noto Sans CJK SC"
    )


async def composite_broll(
    avatar_video_url: str,
    broll_clips: list[dict],
    section_order: list[str],
    sections: dict,
    output_dir: str,
) -> str:
    """Insert B-roll cutaways into avatar video at AI-directed timestamps.

    Args:
        avatar_video_url: URL of the full avatar talking-head video.
        broll_clips: [{url, insert_after_char, duration_seconds, type, prompt}, ...]
        section_order: Ordered section IDs.
        sections: {id: {text, duration_seconds, ...}} from structured_content.
        output_dir: Where to write the final video.

    Returns:
        Local file path of the composited video.
    """
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
            clip_dur = min(clip.get("duration_seconds") or 5, 6.0)
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
                    vf = f"crop={cw}:{ch}:(iw-{cw})/2:(ih-{ch})/2,scale=1080:1920:flags=lanczos,setsar=1"
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
            # Clamp: don't insert before cursor or in last 3 seconds
            if insert_time < cursor + 1.0 or insert_time > avatar_duration - 3.0:
                logger.info("Skipping B-roll at %.1fs (cursor=%.1f, end=%.1f)", insert_time, cursor, avatar_duration)
                continue

            # Avatar segment(s) before this B-roll insert
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
            subtitle_text = _extract_subtitle_text(char_pos, use_dur, total_text, avatar_duration)
            subtitle_filter = _drawtext_filter(subtitle_text)
            scale_filter = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:-1:-1:color=black,setsar=1"
            vf = f"{scale_filter},{subtitle_filter}" if subtitle_text else scale_filter
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
