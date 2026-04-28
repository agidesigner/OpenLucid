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

from app.adapters.video._tagging import (
    SYNTHETIC_AVATAR_TAG_CATEGORIES,
    SYNTHETIC_VOICE_TAG_CATEGORIES,
    synthetic_avatar_tag_tokens,
    synthetic_voice_tag_tokens,
)
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

    @staticmethod
    def _parse_avatar_item(item: dict) -> Avatar | None:
        """Map one /avatars/public item to an Avatar. Surfaces the same
        ``extras`` keys as the chanjing adapter (`native_aspect_ratio`,
        `tag_ids`) so the picker filter logic is provider-agnostic.
        """
        raw_id = item.get("id")
        if not raw_id and raw_id != 0:  # 0 is a valid Jogg id (int)
            logger.warning("Jogg avatar item missing id, skipping: %s", item)
            return None
        item_id = str(raw_id)

        # Jogg encodes aspect as an int code per avatar
        # (0 = portrait 9:16, 1 = landscape 16:9). Map to the same enum
        # chanjing's _native_aspect_ratio emits, so the chip filter and
        # extras key match across providers.
        ratio_code = item.get("aspect_ratio")
        if ratio_code == 0:
            native_ratio: str | None = "portrait"
        elif ratio_code == 1:
            native_ratio = "landscape"
        else:
            native_ratio = None

        gender = _normalize_gender(item.get("gender"))
        age = _normalize_age(item.get("age"), kind="avatar")

        extras: dict = {}
        if native_ratio is not None:
            extras["native_aspect_ratio"] = native_ratio
        # Jogg has no native tag taxonomy — synthetic tokens are the
        # only chip-filter signal we have. Same token format as chanjing
        # (gender:*, age:*, aspect:*), so one frontend filter covers both.
        tokens = synthetic_avatar_tag_tokens(
            gender=gender, age=age, native_aspect_ratio=native_ratio,
        )
        if tokens:
            extras["tag_ids"] = tokens

        return Avatar(
            id=item_id,
            name=item.get("name", ""),
            gender=gender,
            preview_image_url=item.get("cover_url", ""),
            preview_video_url=item.get("video_url"),
            age=age,
            extras=extras,
            raw=item,
        )

    async def list_avatars(
        self, page: int = 1, page_size: int = 50, sort: str | None = None,
    ) -> list[Avatar]:
        # ``sort`` accepted for cross-provider parity; jogg's
        # /avatars/public has no sort parameter, so it's silently
        # ignored. Adding it to the signature lets the service layer
        # call all providers uniformly without dispatch.
        body = await self._request(
            "GET",
            "/avatars/public",
            params={"page": page, "page_size": page_size},
        )
        items = (body.get("data") or {}).get("avatars") or []
        avatars: list[Avatar] = []
        seen_ids: set[str] = set()
        for item in items:
            avatar = self._parse_avatar_item(item)
            if avatar is None:
                continue
            if avatar.id in seen_ids:
                logger.warning("Jogg avatar duplicate id %s, skipping", avatar.id)
                continue
            seen_ids.add(avatar.id)
            avatars.append(avatar)
        return avatars

    async def list_all_avatars(
        self, page_size: int = 100, max_pages: int = 20,
        sort: str | None = None,
    ) -> list[Avatar]:
        """Walk /avatars/public until empty.

        Why: jogg paginates with `page` / `page_size` and a single-page
        fetch only exposes the first slice of the public library — same
        bug class as chanjing's list_common_dp.
        """
        seen: set[str] = set()
        out: list[Avatar] = []
        for page in range(1, max_pages + 1):
            batch = await self.list_avatars(page=page, page_size=page_size)
            if not batch:
                break
            new_count = 0
            for av in batch:
                if av.id in seen:
                    continue
                seen.add(av.id)
                out.append(av)
                new_count += 1
            # If a page returned only duplicates, the server is looping —
            # bail rather than spin to max_pages.
            if new_count == 0:
                break
        logger.info(
            "Jogg list_all_avatars: %d avatars across %d page(s)",
            len(out), page,
        )
        return out

    @staticmethod
    def _parse_voice_item(item: dict) -> Voice | None:
        raw_id = item.get("voice_id")
        if not raw_id:
            logger.warning("Jogg voice item missing voice_id, skipping: %s", item)
            return None
        item_id = str(raw_id)
        gender = _normalize_gender(item.get("gender"))
        age = _normalize_age(item.get("age"), kind="voice")
        language = item.get("language")

        extras: dict = {}
        tokens = synthetic_voice_tag_tokens(
            gender=gender, age=age, language=language,
        )
        if tokens:
            extras["tag_ids"] = tokens

        return Voice(
            id=item_id,
            name=item.get("name", ""),
            gender=gender,
            language=language,
            sample_url=item.get("audio_url", ""),
            age=age,
            extras=extras,
            raw=item,
        )

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
            voice = self._parse_voice_item(item)
            if voice is None:
                continue
            if voice.id in seen_ids:
                logger.warning("Jogg voice duplicate id %s, skipping", voice.id)
                continue
            seen_ids.add(voice.id)
            voices.append(voice)
        return voices

    async def list_avatar_tags(self) -> list[dict]:
        """Jogg has no server-side tag taxonomy — the picker still gets
        the cross-provider synthetic chips so the UI is identical to
        chanjing's. Returning an empty list here would force the
        frontend to branch on provider, defeating the abstraction."""
        return [c.model_dump() for c in SYNTHETIC_AVATAR_TAG_CATEGORIES]

    async def list_voice_tags(self) -> list[dict]:
        return [c.model_dump() for c in SYNTHETIC_VOICE_TAG_CATEGORIES]

    async def list_all_voices(
        self, page_size: int = 100, max_pages: int = 20,
    ) -> list[Voice]:
        """Walk /voices until empty. Same rationale as list_all_avatars."""
        seen: set[str] = set()
        out: list[Voice] = []
        for page in range(1, max_pages + 1):
            batch = await self.list_voices(page=page, page_size=page_size)
            if not batch:
                break
            new_count = 0
            for v in batch:
                if v.id in seen:
                    continue
                seen.add(v.id)
                out.append(v)
                new_count += 1
            if new_count == 0:
                break
        logger.info(
            "Jogg list_all_voices: %d voices across %d page(s)",
            len(out), page,
        )
        return out

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
