"""Jogg.ai video provider adapter.

API docs: https://docs.jogg.ai/api-reference/v2/
Base URL: https://api.jogg.ai/v2

Auth: static `x-api-key: <key>` header. No token refresh needed.

Business endpoints:
    POST /v2/create_video_from_avatar  — create talking-avatar video task
    GET  /v2/avatar_video/{id}         — poll status
    GET  /v2/avatars/public            — list public avatars
    GET  /v2/voices                    — list public voices

All success responses follow: {code: 0, msg: "Success", data: ...}
"""

from __future__ import annotations

import asyncio
import logging
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

JOGG_BASE_URL = "https://api.jogg.ai/v2"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3

_JOGG_STATUS_MAP: dict[str, JobStatus] = {
    "pending": "pending",
    "queued": "pending",
    "processing": "processing",
    "running": "processing",
    # Jogg's real terminal states (observed 2026-04): "success" / "fail".
    # "completed" kept for forward-compat in case API changes.
    "success": "completed",
    "completed": "completed",
    "fail": "failed",
    "failed": "failed",
    "error": "failed",
}


def _normalize_gender(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in ("male", "m"):
        return "male"
    if s in ("female", "f"):
        return "female"
    return None


# Jogg uses two different age vocabularies — one for avatars, one for voices.
_JOGG_AVATAR_AGE_MAP = {
    "young_adult": "young",
    "adult": "adult",
    "senior": "senior",
}
_JOGG_VOICE_AGE_MAP = {
    "young": "young",
    "middle_aged": "adult",
    "old": "senior",
}


def _normalize_age(raw: str | None, kind: str) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().lower()
    table = _JOGG_VOICE_AGE_MAP if kind == "voice" else _JOGG_AVATAR_AGE_MAP
    return table.get(s)


class JoggVideoProvider:
    """Jogg.ai video provider implementation."""

    provider_name = "jogg"

    def __init__(self, api_key: str, base_url: str = JOGG_BASE_URL):
        if not api_key:
            raise AppError("INVALID_CREDENTIALS", "Jogg requires api_key", 400)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    # ----- low-level HTTP with retry -----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        url = f"{self._base_url}{path}"
        headers = {"x-api-key": self._api_key}

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
                        "JOGG_HTTP_ERROR",
                        f"Jogg {method} {path} returned {status}: {e.response.text if e.response else ''}",
                        502,
                    ) from e
                if attempt < MAX_RETRIES - 1:
                    wait = (attempt + 1) * 2
                    logger.warning(
                        "Jogg %s %s failed (attempt %d/%d), retrying in %ds: %s",
                        method, path, attempt + 1, MAX_RETRIES, wait, e,
                    )
                    await asyncio.sleep(wait)
                continue
            except httpx.HTTPError as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    wait = (attempt + 1) * 2
                    logger.warning(
                        "Jogg %s %s network error (attempt %d/%d), retrying in %ds: %s",
                        method, path, attempt + 1, MAX_RETRIES, wait, e,
                    )
                    await asyncio.sleep(wait)
                continue

            if body.get("code") != 0:
                msg = body.get("msg", "unknown error")
                raise AppError(
                    "JOGG_API_ERROR",
                    f"Jogg {path} returned code={body.get('code')}: {msg}",
                    502,
                )
            return body

        raise AppError(
            "JOGG_HTTP_ERROR",
            f"Jogg {method} {path} failed after {MAX_RETRIES} attempts: {last_err}",
            502,
        )

    # ----- VideoProvider interface -----

    async def list_avatars(self, page: int = 1, page_size: int = 50) -> list[Avatar]:
        body = await self._request(
            "GET",
            "/avatars/public",
            params={"page": page, "page_size": page_size},
        )
        items = (body.get("data") or {}).get("avatars") or []
        avatars: list[Avatar] = []
        seen_ids: set[str] = set()
        for item in items:
            raw_id = item.get("id")
            if not raw_id and raw_id != 0:  # 0 is a valid Jogg id (int)
                logger.warning("Jogg avatar item missing id, skipping: %s", item)
                continue
            item_id = str(raw_id)
            if item_id in seen_ids:
                logger.warning("Jogg avatar duplicate id %s, skipping", item_id)
                continue
            seen_ids.add(item_id)
            # Jogg returns ``aspect_ratio`` as an int code on each avatar
            # (0 = portrait 9:16, 1 = landscape 16:9). Surface it on
            # ``extras`` so the avatar picker can filter by the user's
            # chosen format and only show templates that match.
            extras: dict = {}
            ratio_code = item.get("aspect_ratio")
            if ratio_code == 0:
                extras["aspect_ratio"] = "portrait"
            elif ratio_code == 1:
                extras["aspect_ratio"] = "landscape"
            avatars.append(
                Avatar(
                    id=item_id,
                    name=item.get("name", ""),
                    gender=_normalize_gender(item.get("gender")),
                    preview_image_url=item.get("cover_url", ""),
                    preview_video_url=item.get("video_url"),
                    age=_normalize_age(item.get("age"), kind="avatar"),
                    extras=extras,
                    raw=item,
                )
            )
        return avatars

    async def list_voices(self, page: int = 1, page_size: int = 50) -> list[Voice]:
        body = await self._request(
            "GET",
            "/voices",
            params={"page": page, "page_size": page_size},
        )
        items = (body.get("data") or {}).get("voices") or []
        voices: list[Voice] = []
        seen_ids: set[str] = set()
        for item in items:
            raw_id = item.get("voice_id")
            if not raw_id:
                logger.warning("Jogg voice item missing voice_id, skipping: %s", item)
                continue
            item_id = str(raw_id)
            if item_id in seen_ids:
                logger.warning("Jogg voice duplicate id %s, skipping", item_id)
                continue
            seen_ids.add(item_id)
            voices.append(
                Voice(
                    id=item_id,
                    name=item.get("name", ""),
                    gender=_normalize_gender(item.get("gender")),
                    language=item.get("language"),
                    sample_url=item.get("audio_url", ""),
                    age=_normalize_age(item.get("age"), kind="voice"),
                    raw=item,
                )
            )
        return voices

    async def create_avatar_video(self, req: CreateVideoRequest) -> str:
        if len(req.script) > 4000:
            raise AppError("SCRIPT_TOO_LONG", "Script must be <= 4000 chars", 400)

        # Jogg avatar_id is an integer in the API; we accept str at the Protocol
        # boundary and cast here.
        try:
            avatar_id_int = int(req.avatar_id)
        except (TypeError, ValueError) as e:
            raise AppError(
                "INVALID_AVATAR_ID",
                f"Jogg avatar_id must be a numeric string, got {req.avatar_id!r}",
                400,
            ) from e

        body_payload: dict[str, Any] = {
            "avatar": {
                "avatar_type": 0,  # 0 = public avatar
                "avatar_id": avatar_id_int,
            },
            "voice": {
                "type": "script",
                "script": req.script,
                "voice_id": req.voice_id,
            },
            "aspect_ratio": req.aspect_ratio,
            "screen_style": 1,  # 1 = full screen
            "caption": req.caption,
        }
        if req.name:
            body_payload["video_name"] = req.name

        body = await self._request(
            "POST",
            "/create_video_from_avatar",
            json_body=body_payload,
        )
        data = body.get("data") or {}
        task_id = data.get("video_id")
        if not isinstance(task_id, str) or not task_id:
            raise AppError(
                "JOGG_API_MALFORMED",
                f"Jogg create_video_from_avatar returned unexpected data: {body}",
                502,
            )
        return task_id

    async def synthesize_speech(self, voice_id: str, text: str) -> str:
        """Jogg v2 has no standalone TTS endpoint — fall back to the voice's
        pre-recorded sample URL so the audition button still works.

        The `text` parameter is ignored (the sample says whatever the voice
        provider recorded). The frontend should display a tiny note for Jogg
        voices clarifying this is a generic sample, not the user's script.
        """
        if not voice_id:
            raise AppError("INVALID_VOICE_ID", "Jogg audition requires voice_id", 400)
        # Look up the voice in the public catalog. We page through until we find
        # it (limit a few pages to avoid pathological scans).
        for page in range(1, 6):  # up to 500 voices
            voices = await self.list_voices(page=page, page_size=100)
            for v in voices:
                if v.id == voice_id:
                    if not v.sample_url:
                        raise AppError(
                            "JOGG_NO_SAMPLE",
                            f"Jogg voice {voice_id} has no sample URL",
                            502,
                        )
                    return v.sample_url
            if len(voices) < 100:
                break  # last page
        raise AppError(
            "JOGG_VOICE_NOT_FOUND",
            f"Jogg voice {voice_id} not found in public catalog",
            404,
        )

    async def get_video_status(self, task_id: str) -> VideoStatus:
        body = await self._request(
            "GET",
            f"/avatar_video/{task_id}",
        )
        data = body.get("data") or {}
        raw_status = str(data.get("status", "pending")).lower()
        mapped: JobStatus = _JOGG_STATUS_MAP.get(raw_status, "failed")
        error_message = None
        if mapped == "failed":
            error_message = body.get("msg") or f"Jogg status: {raw_status}"
        return VideoStatus(
            task_id=task_id,
            status=mapped,
            progress=None,  # Jogg doesn't expose progress
            video_url=data.get("video_url") or None,
            cover_url=data.get("cover_url") or None,
            duration_seconds=None,  # Jogg doesn't expose duration in this endpoint
            error_message=error_message,
            raw=data,
        )
