"""Chanjing (蝉镜) video provider adapter.

API docs: https://doc.chanjing.cc/api/
Base URL: https://open-api.chanjing.cc

Auth flow:
    POST /open/v1/access_token  body: {app_id, secret_key}
    -> data: {access_token, expire_in}  (expire_in is a Unix epoch, NOT a TTL)

Business endpoints (all use header `access_token: <token>`):
    POST /open/v1/create_video           — create talking-avatar video task
    GET  /open/v1/video?id=X             — poll status
    GET  /open/v1/list_common_dp         — list public avatars
    GET  /open/v1/list_common_audio      — list public voices

All success responses follow: {trace_id, code: 0, msg: "success", data: ...}
Non-zero `code` means failure; `msg` carries the error description.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from app.adapters.video.base import (
    Avatar,
    AspectRatio,
    CreateVideoRequest,
    JobStatus,
    Voice,
    VideoStatus,
)
from app.exceptions import AppError

logger = logging.getLogger(__name__)

CHANJING_BASE_URL = "https://open-api.chanjing.cc"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3

# TTS audition polling — short clips so we shouldn't need long waits
AUDITION_POLL_INTERVAL = 1.0  # seconds
AUDITION_POLL_MAX_TRIES = 30  # → 30s ceiling for a ~20-char clip


# ── Module-level access_token cache ─────────────────────────────────
#
# Chanjing tokens have a quirky semantic: "once a new token is obtained, the
# previous one becomes invalid". If two parallel requests each create their
# own ChanjingVideoProvider instance, each fetches a new token, the second
# kills the first, and whichever request used the now-invalidated token fails
# with "AccessToken已失效".
#
# Fix: share the token cache (and the refresh lock) across all instances that
# use the same (app_id, secret_key) tuple. Per credential pair we'll fetch at
# most one token per day for the whole process lifetime.

_token_cache: dict[tuple[str, str], dict[str, Any]] = {}
_token_cache_lock = asyncio.Lock()  # protects creation of per-key entries


async def _get_token_state(app_id: str, secret_key: str) -> dict[str, Any]:
    """Return the shared token state dict for these credentials, creating it lazily."""
    key = (app_id, secret_key)
    async with _token_cache_lock:
        state = _token_cache.get(key)
        if state is None:
            state = {
                "token": None,        # str | None
                "expire_at": 0.0,     # Unix epoch seconds
                "lock": asyncio.Lock(),  # protects refresh for this key
            }
            _token_cache[key] = state
        return state


# Chanjing's TTS rejects any emoji in the script with `code=50000: 输入文本不可以
# 包含 emoji`. Strip them silently before sending so the user doesn't have to
# clean up scripts pasted from real-world content (which always have emoji).
# Hand-rolled Unicode ranges (no third-party `regex` package) — covers BMP +
# supplementary planes + ZWJ glue + variation selectors.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F680-\U0001F6FF"  # Transport and Map
    "\U0001F700-\U0001F77F"  # Alchemical
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess + Symbols and Pictographs Ext-A
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Ext-A
    "\u2600-\u26FF"          # Miscellaneous Symbols (☀ ☁ ⚠ etc.)
    "\u2700-\u27BF"          # Dingbats (✂ ✈ ✉ etc.)
    "\u2300-\u23FF"          # Misc Technical (⏰ ⌛ etc.)
    "\u200D"                 # Zero-width joiner
    "\uFE0F"                 # Variation Selector-16
    "]"
)


def _strip_emoji(text: str) -> str:
    """Remove emoji + ZWJ + variation selectors, then collapse extra spaces."""
    cleaned = _EMOJI_PATTERN.sub("", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _normalize_gender(raw: str | None) -> str | None:
    """Chanjing returns 男/女 or 'male'/'female'; normalize to lowercase enum."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in ("男", "male", "m"):
        return "male"
    if s in ("女", "female", "f"):
        return "female"
    return None


def _age_from_chanjing_tags(tag_names: list[str] | None) -> str | None:
    """Parse age from Chanjing avatar tag_names list (e.g. '中年', '青年')."""
    if not tag_names:
        return None
    joined = " ".join(str(t) for t in tag_names)
    if any(k in joined for k in ("老年", "老人", "老者", "senior", "old")):
        return "senior"
    if any(k in joined for k in ("青年", "年轻", "少年", "young")):
        return "young"
    if any(k in joined for k in ("中年", "成年", "adult", "middle")):
        return "adult"
    return None


def _age_from_voice_name(name: str | None) -> str | None:
    """Best-effort parse of age from Chanjing voice name (no structured field)."""
    if not name:
        return None
    if any(k in name for k in ("老", "senior", "old")):
        return "senior"
    if any(k in name for k in ("小哥", "小芸", "小妹", "青年", "年轻", "少年", "young")):
        return "young"
    return None  # leave unknown rather than guessing "adult"


def _aspect_to_canvas(aspect_ratio: AspectRatio) -> tuple[int, int, int, int, int, int]:
    """Return (canvas_w, canvas_h, person_x, person_y, person_w, person_h).

    Chanjing's `create_video` requires explicit person geometry. We pick sensible
    defaults based on aspect_ratio matching the doc example (portrait y=0).
    """
    if aspect_ratio == "portrait":
        return (1080, 1920, 0, 0, 1080, 1920)
    if aspect_ratio == "landscape":
        return (1920, 1080, 0, 0, 1920, 1080)
    # square
    return (1080, 1080, 0, 0, 1080, 1080)


# Subtitle presets live in ``subtitle_styles.py`` now so the B-roll compositor
# and this provider-side burn-in share the exact same style configuration.
from app.adapters.video.subtitle_styles import (
    compute_font_size,
    resolve_style,
)


def _subtitle_config(
    aspect_ratio: AspectRatio,
    show: bool,
    style: str = "classic",
    color_override: str | None = None,
    stroke_override: str | None = None,
) -> dict[str, Any]:
    """Build subtitle_config with proper positioning + styled typography.

    Without explicit position, Chanjing defaults to y=0 (top of screen).
    Style presets control size, position, and stroke weight — not just color.
    Custom color/stroke overrides are applied on top of the chosen preset.
    """
    if not show:
        return {"show": False}

    style_params = resolve_style(style, color_override, stroke_override)
    canvas_w, canvas_h, *_ = _aspect_to_canvas(aspect_ratio)
    text_w = int(canvas_w * 0.9)
    text_x = (canvas_w - text_w) // 2
    text_y = int(canvas_h * style_params["y_ratio"])
    text_h = int(canvas_h * 0.12)
    font_size = compute_font_size(aspect_ratio, style)

    return {
        "show": True,
        "x": text_x,
        "y": text_y,
        "width": text_w,
        "height": text_h,
        "font_size": font_size,
        "color": style_params["color"],
        "stroke_color": style_params["stroke_color"],
        "stroke_width": style_params["stroke_width"],
    }


def _map_status(code: int) -> JobStatus:
    """Chanjing status int -> common JobStatus enum.

    From doc: 10 = in progress, 30 = success, 4X/5X = failed.
    """
    if code == 30:
        return "completed"
    if code == 10:
        return "processing"
    if code >= 40:
        return "failed"
    return "pending"


class ChanjingVideoProvider:
    """Chanjing video provider implementation.

    Token state is shared at the module level keyed by (app_id, secret_key) — see
    `_token_cache` above — so multiple instances using the same credentials
    share one token and one refresh lock. Each instance is otherwise stateless.
    """

    provider_name = "chanjing"

    def __init__(self, app_id: str, secret_key: str, base_url: str = CHANJING_BASE_URL):
        if not app_id or not secret_key:
            raise AppError("INVALID_CREDENTIALS", "Chanjing requires both app_id and secret_key", 400)
        self._app_id = app_id
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")

    # ----- token management -----

    async def _get_access_token(self) -> str:
        """Return a valid access_token, refreshing if expired or missing.

        State is shared across all instances with the same credentials via the
        module-level `_token_cache`. The per-key lock prevents thundering herd
        on first fetch and on TTL refresh.
        """
        state = await _get_token_state(self._app_id, self._secret_key)

        # Fast path: token is still valid (with 60s safety margin)
        token = state["token"]
        if token and time.time() < (state["expire_at"] - 60):
            return token

        # Slow path: refresh under the per-key lock
        async with state["lock"]:
            # Double-check after acquiring the lock — another concurrent caller
            # may have just refreshed it.
            token = state["token"]
            if token and time.time() < (state["expire_at"] - 60):
                return token

            url = f"{self._base_url}/open/v1/access_token"
            payload = {"app_id": self._app_id, "secret_key": self._secret_key}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                try:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    raise AppError(
                        "CHANJING_AUTH_HTTP_ERROR",
                        f"Failed to reach Chanjing access_token endpoint: {e}",
                        502,
                    ) from e

            body = resp.json()
            if body.get("code") != 0:
                raise AppError(
                    "CHANJING_AUTH_FAILED",
                    f"Chanjing rejected credentials: {body.get('msg', 'unknown error')}",
                    401,
                )
            data = body.get("data") or {}
            token = data.get("access_token")
            expire_at = data.get("expire_in")
            if not token or not expire_at:
                raise AppError(
                    "CHANJING_AUTH_MALFORMED",
                    f"Chanjing access_token response missing fields: {body}",
                    502,
                )
            state["token"] = token
            state["expire_at"] = float(expire_at)
            logger.info("Chanjing access_token refreshed, expires at %s", state["expire_at"])
            return token

    # ----- low-level HTTP with retry -----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Authenticated request with retry on transient errors."""
        token = await self._get_access_token()
        url = f"{self._base_url}{path}"
        headers = {"access_token": token}

        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    resp = await client.request(
                        method, url, params=params, json=json_body, headers=headers
                    )
                    resp.raise_for_status()
                    body = resp.json()
            except httpx.HTTPStatusError as e:
                last_err = e
                status = e.response.status_code if e.response else 0
                if 400 <= status < 500 and status != 429:
                    raise AppError(
                        "CHANJING_HTTP_ERROR",
                        f"Chanjing {method} {path} returned {status}: {e.response.text if e.response else ''}",
                        502,
                    ) from e
                if attempt < MAX_RETRIES - 1:
                    wait = (attempt + 1) * 2
                    logger.warning(
                        "Chanjing %s %s failed (attempt %d/%d), retrying in %ds: %s",
                        method, path, attempt + 1, MAX_RETRIES, wait, e,
                    )
                    await asyncio.sleep(wait)
                continue
            except httpx.HTTPError as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    wait = (attempt + 1) * 2
                    logger.warning(
                        "Chanjing %s %s network error (attempt %d/%d), retrying in %ds: %s",
                        method, path, attempt + 1, MAX_RETRIES, wait, e,
                    )
                    await asyncio.sleep(wait)
                continue

            # Success path: validate body code
            code = body.get("code")
            if code == 10400:
                # Token expired server-side — invalidate cache, refresh, and retry once
                logger.warning("Chanjing token expired (10400), refreshing and retrying %s %s", method, path)
                state = await _get_token_state(self._app_id, self._secret_key)
                state["token"] = None
                state["expire_at"] = 0.0
                token = await self._get_access_token()
                headers = {"access_token": token}
                continue  # retry with fresh token
            if code != 0:
                msg = body.get("msg", "unknown error")
                raise AppError(
                    "CHANJING_API_ERROR",
                    f"Chanjing {path} returned code={code}: {msg}",
                    502,
                )
            return body

        raise AppError(
            "CHANJING_HTTP_ERROR",
            f"Chanjing {method} {path} failed after {MAX_RETRIES} attempts: {last_err}",
            502,
        )

    # ----- VideoProvider interface -----

    async def list_avatars(self, page: int = 1, page_size: int = 50) -> list[Avatar]:
        body = await self._request(
            "GET",
            "/open/v1/list_common_dp",
            params={"page": page, "size": page_size},
        )
        items = (body.get("data") or {}).get("list") or []
        avatars: list[Avatar] = []
        seen_ids: set[str] = set()
        for item in items:
            # Defensive: skip items with missing id (otherwise the frontend
            # x-for collides on the empty-string fallback key and crashes).
            raw_id = item.get("id")
            if not raw_id:
                logger.warning("Chanjing avatar item missing id, skipping: %s", item)
                continue
            item_id = str(raw_id)
            if item_id in seen_ids:
                logger.warning("Chanjing avatar duplicate id %s, skipping", item_id)
                continue
            seen_ids.add(item_id)

            figures = item.get("figures") or []
            first_figure = figures[0] if figures else {}
            extras: dict = {}
            if first_figure.get("type"):
                extras["figure_type"] = first_figure["type"]
            else:
                # Defensive: if Chanjing ever returns an avatar without a
                # figures[0].type, submit_video has no hint and falls back to
                # the `whole_body` default — which may be rejected with
                # code=50000. Log so ops can spot and escalate.
                logger.warning(
                    "Chanjing avatar %s has no figures[0].type; "
                    "submit_video will fall back to whole_body (may fail)",
                    item_id,
                )
            # Chanjing avatars carry their officially-paired voice id — use it
            # as the highest-confidence default in the frontend.
            if item.get("audio_man_id"):
                extras["paired_voice_id"] = str(item["audio_man_id"])
            avatars.append(
                Avatar(
                    id=item_id,
                    name=item.get("name", ""),
                    gender=_normalize_gender(item.get("gender")),
                    preview_image_url=first_figure.get("cover", ""),
                    preview_video_url=first_figure.get("preview_video_url"),
                    age=_age_from_chanjing_tags(item.get("tag_names")),
                    extras=extras,
                    raw=item,
                )
            )
        return avatars

    async def list_voices(self, page: int = 1, page_size: int = 50) -> list[Voice]:
        body = await self._request(
            "GET",
            "/open/v1/list_common_audio",
            params={"page": page, "size": page_size},
        )
        items = (body.get("data") or {}).get("list") or []
        voices: list[Voice] = []
        seen_ids: set[str] = set()
        for item in items:
            raw_id = item.get("id")
            if not raw_id:
                logger.warning("Chanjing voice item missing id, skipping: %s", item)
                continue
            item_id = str(raw_id)
            if item_id in seen_ids:
                logger.warning("Chanjing voice duplicate id %s, skipping", item_id)
                continue
            seen_ids.add(item_id)

            name = item.get("name", "")
            voices.append(
                Voice(
                    id=item_id,
                    name=name,
                    gender=_normalize_gender(item.get("gender")),
                    language=item.get("lang"),
                    sample_url=item.get("audition", ""),
                    age=_age_from_voice_name(name),
                    raw=item,
                )
            )
        return voices

    async def create_avatar_video(self, req: CreateVideoRequest) -> str:
        if len(req.script) > 4000:
            raise AppError("SCRIPT_TOO_LONG", "Chanjing script must be <= 4000 chars", 400)

        # Chanjing TTS rejects emoji — silently strip them before sending so
        # users can paste real-world content (social copy, marketing scripts)
        # without having to manually clean it up first.
        script = _strip_emoji(req.script)
        if script != req.script:
            logger.info(
                "Chanjing: stripped emoji from script (%d -> %d chars)",
                len(req.script), len(script),
            )

        canvas_w, canvas_h, px, py, pw, ph = _aspect_to_canvas(req.aspect_ratio)

        # figure_type is REQUIRED in practice for public avatars even though doc
        # marks it optional. Caller must echo it from Avatar.extras after listing.
        figure_type = (req.provider_extras or {}).get("figure_type", "whole_body")

        person: dict[str, Any] = {
            "id": req.avatar_id,
            "x": px,
            "y": py,
            "width": pw,
            "height": ph,
            "figure_type": figure_type,
            "drive_mode": "random",
        }
        audio: dict[str, Any] = {
            "type": "tts",
            "tts": {
                "text": [script],  # Chanjing requires array, not string
                "speed": 1,
                "audio_man": req.voice_id,
            },
            "wav_url": "",
            "volume": 100,
            "language": "cn",
        }
        body_payload: dict[str, Any] = {
            "person": person,
            "audio": audio,
            "subtitle_config": _subtitle_config(
                req.aspect_ratio, req.caption, req.subtitle_style,
                req.subtitle_color, req.subtitle_stroke,
            ),
            "screen_width": canvas_w,
            "screen_height": canvas_h,
        }

        body = await self._request(
            "POST",
            "/open/v1/create_video",
            json_body=body_payload,
        )
        # data is the task_id string directly per docs
        task_id = body.get("data")
        if not isinstance(task_id, str) or not task_id:
            raise AppError(
                "CHANJING_API_MALFORMED",
                f"Chanjing create_video returned unexpected data: {body}",
                502,
            )
        return task_id

    async def synthesize_speech(self, voice_id: str, text: str) -> str:
        """Submit a TTS task and poll until the audio URL is ready.

        Endpoints:
            POST /open/v1/create_audio_task  → returns task_id
            POST /open/v1/audio_task_state    → returns {data: {status, full: {url}}}
            status: 1 = in progress, 9 = complete (success or failure)
        """
        if not voice_id:
            raise AppError("INVALID_VOICE_ID", "Chanjing TTS requires voice_id", 400)
        clean = _strip_emoji(text or "")
        if not clean:
            raise AppError("EMPTY_TEXT", "Cannot synthesize empty text", 400)
        if len(clean) > 4000:
            raise AppError("TEXT_TOO_LONG", "Chanjing TTS text must be <= 4000 chars", 400)

        # 1) Submit
        submit_body = await self._request(
            "POST",
            "/open/v1/create_audio_task",
            json_body={
                "audio_man": voice_id,
                "speed": 1,
                "pitch": 1,
                "text": {"text": clean, "plain_text": clean},
            },
        )
        data = submit_body.get("data") or {}
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise AppError(
                "CHANJING_TTS_MALFORMED",
                f"Chanjing create_audio_task returned unexpected data: {submit_body}",
                502,
            )

        # 2) Poll
        for _ in range(AUDITION_POLL_MAX_TRIES):
            await asyncio.sleep(AUDITION_POLL_INTERVAL)
            poll_body = await self._request(
                "POST",
                "/open/v1/audio_task_state",
                json_body={"task_id": task_id},
            )
            poll_data = poll_body.get("data") or {}
            status = poll_data.get("status")
            if status == 9:
                # Complete (success or failure)
                full = poll_data.get("full") or {}
                audio_url = full.get("url") or ""
                if not audio_url:
                    err = poll_data.get("errMsg") or poll_data.get("errReason") or "unknown error"
                    raise AppError(
                        "CHANJING_TTS_FAILED",
                        f"Chanjing TTS failed: {err}",
                        502,
                    )
                return audio_url
            # status == 1 → still in progress, keep polling

        raise AppError(
            "CHANJING_TTS_TIMEOUT",
            f"Chanjing TTS task {task_id} did not finish in {AUDITION_POLL_MAX_TRIES * AUDITION_POLL_INTERVAL:.0f}s",
            504,
        )

    async def get_video_status(self, task_id: str) -> VideoStatus:
        body = await self._request(
            "GET",
            "/open/v1/video",
            params={"id": task_id},
        )
        data = body.get("data") or {}
        provider_status = int(data.get("status", 0))
        mapped = _map_status(provider_status)
        error_message = None
        if mapped == "failed":
            error_message = data.get("msg") or f"Chanjing status code {provider_status}"
        return VideoStatus(
            task_id=task_id,
            status=mapped,
            progress=data.get("progress"),
            video_url=data.get("video_url") or None,
            cover_url=data.get("preview_url") or None,
            duration_seconds=data.get("duration"),
            error_message=error_message,
            raw=data,
        )

    # ── AI Creation (B-roll generation) ────────────────────────

    async def upload_temp_file(self, file_bytes: bytes, filename: str, service: str = "ai_creation") -> str:
        """Upload a file to Chanjing's temp storage and return the public URL.

        Two-step process:
          1. GET /open/v1/common/create_upload_url → {sign_url, full_path, mime_type}
          2. PUT file bytes to sign_url
        Returns full_path (publicly accessible URL, auto-deleted after 30 days).
        """
        # 1. Get signed upload URL
        body = await self._request(
            "GET",
            "/open/v1/common/create_upload_url",
            params={"service": service, "name": filename},
        )
        data = body.get("data") or {}
        sign_url = data.get("sign_url")
        full_path = data.get("full_path")
        mime_type = data.get("mime_type") or "application/octet-stream"
        if not sign_url or not full_path:
            raise AppError("CHANJING_UPLOAD_FAILED", f"create_upload_url returned: {body}", 502)

        # 2. PUT file to signed URL
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.put(sign_url, content=file_bytes, headers={"Content-Type": mime_type})
            resp.raise_for_status()

        logger.info("Chanjing file uploaded: %s -> %s (%.1f KB)", filename, full_path[:60], len(file_bytes) / 1024)
        return full_path

    async def submit_broll_clip(
        self,
        prompt: str,
        duration: int = 6,
        aspect_ratio: str = "9:16",
        model_code: str = "Doubao-Seedance-1.0-pro",
        *,
        style_references: list["StyleReference"] | None = None,
        first_frame: "FirstFrame | None" = None,
        last_frame: "LastFrame | None" = None,
    ) -> str:
        """Submit an AI video generation task for a B-roll clip.

        Reference handling per chanjing API capability:

          - ``style_references`` (Class A, soft) — chanjing's ai_creation
            endpoint has NO native style-reference field. We DROP these
            silently. If a reference carries a ``description``, we may
            optionally append a hint to the prompt (currently disabled
            to keep behavior predictable; revisit if needed).

          - ``first_frame`` (Class B, hard) — maps to ``ref_img_url``
            (which Doubao-Seedance treats as the video's starting
            frame). Aspect must satisfy [0.5, 2.0]; the API will
            reject otherwise. Other model_codes have their own
            constraints — propagate the API error verbatim.

          - ``last_frame`` — chanjing/Doubao currently doesn't support
            end-frame anchoring; raise ``UnsupportedReferenceMode``.

        Hailuo within the chanjing umbrella is t2v-only; first_frame
        is also unsupported there.
        """
        from app.adapters.video.base import (
            FirstFrame,
            LastFrame,
            StyleReference,
            UnsupportedReferenceMode,
        )
        # Compile-time-style guard for typing; runtime use is the point.
        _ = (FirstFrame, LastFrame, StyleReference)

        if last_frame is not None:
            raise UnsupportedReferenceMode(
                f"chanjing/{model_code} does not support last_frame anchoring"
            )
        if first_frame is not None and "hailuo" in model_code.lower():
            raise UnsupportedReferenceMode(
                f"chanjing/{model_code} (hailuo) is text-to-video only; "
                "first_frame anchoring not supported"
            )

        payload: dict[str, Any] = {
            "ref_prompt": prompt,
            "creation_type": 4,  # video
            "video_duration": max(5, min(duration, 10)),
            "clarity": 720,
            "model_code": model_code,
        }
        if first_frame is not None:
            payload["ref_img_url"] = [first_frame.url]
        # style_references intentionally dropped — chanjing has no
        # native style-ref field. Passing them via ref_img_url would
        # conflate Class A intent with Class B semantics (the bug we
        # spent a release fixing).

        # Most models support aspect_ratio; Hailuo is the exception
        if "hailuo" not in model_code.lower():
            payload["aspect_ratio"] = aspect_ratio
            payload["quality_mode"] = "std"
        try:
            body = await self._request(
                "POST",
                "/open/v1/ai_creation/task/submit",
                json_body=payload,
            )
        except AppError as e:
            # Append hint when chanjing rejects the model_code so the
            # user / dev sees where to look. Chanjing's Kling family
            # uses inconsistent model_code formats per version (v2.1 is
            # ``tx_kling-v2-1-master``, v2.5 is ``kling2.5``); guessing
            # by pattern doesn't work, you must check the per-version
            # doc page.
            if "模型不存在" in str(e):
                raise AppError(
                    "CHANJING_BROLL_SUBMIT_FAILED",
                    (
                        f"chanjing rejected model_code={model_code!r} (模型不存在). "
                        "Each Kling/Doubao/etc. version has its own model_code "
                        "string in chanjing's docs — short forms like 'kling-2.5' "
                        "differ from full forms like 'tx_kling-v2-1-master'. "
                        "Update the registry in setting_service.py with the value "
                        f"verbatim from https://doc.chanjing.cc/api/ai-creation/. "
                        f"Original error: {e}"
                    ),
                    502,
                ) from e
            raise
        unique_id = body.get("data")
        if not unique_id:
            raise AppError(
                "CHANJING_BROLL_SUBMIT_FAILED",
                f"AI creation submit returned unexpected data: {body}",
                502,
            )
        logger.info("Chanjing B-roll submitted: %s (prompt=%s)", unique_id, prompt[:50])
        return str(unique_id)

    async def poll_broll_clip(self, unique_id: str) -> dict:
        """Poll an AI creation task.

        Returns dict with keys:
            status: "processing" | "completed" | "failed"
            output_urls: list[str]  (populated when completed)
            error: str | None
        """
        body = await self._request(
            "GET",
            "/open/v1/ai_creation/task",
            params={"unique_id": unique_id},
        )
        data = body.get("data") or {}
        progress = (data.get("progress_desc") or "").lower()
        output_urls = data.get("output_url") or []

        if "success" in progress:
            return {"status": "completed", "output_urls": output_urls, "error": None}
        elif "error" in progress or "fail" in progress:
            return {
                "status": "failed",
                "output_urls": [],
                "error": data.get("progress_desc") or "AI creation failed",
            }
        else:
            return {"status": "processing", "output_urls": [], "error": None}
