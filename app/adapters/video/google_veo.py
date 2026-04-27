"""Google Veo video provider adapter (Gemini API).

Uses the same API key as the Gemini LLM. Currently supports B-roll generation
(text-to-video and image-to-video). Does NOT support talking avatars — Veo has
no avatar/voice listing or TTS, so list_avatars/list_voices/create_avatar_video
raise NotImplementedError. Only the AI-creation-style B-roll methods are useful.

API docs: https://ai.google.dev/gemini-api/docs/video
Endpoint: https://generativelanguage.googleapis.com/v1beta

Auth: x-goog-api-key header with the same AIza... key used for Gemini LLM.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from app.adapters.video.base import (
    Avatar,
    AspectRatio,
    CreateVideoRequest,
    VideoProvider,
    VideoStatus,
    Voice,
)
from app.exceptions import AppError

logger = logging.getLogger(__name__)

VEO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _aspect_to_veo(aspect_ratio: AspectRatio) -> str:
    """Map our AspectRatio enum to Veo's supported values (16:9 / 9:16 only)."""
    if aspect_ratio == "portrait":
        return "9:16"
    return "16:9"  # landscape + square fall back to landscape


def _veo_duration(duration: int) -> int:
    """Veo supports only 4, 6, or 8 seconds."""
    if duration <= 4:
        return 4
    if duration <= 6:
        return 6
    return 8


class GoogleVeoProvider:
    """Video B-roll provider using Google's Veo API.

    Shares the API key with the user's Gemini LLM config. Talking-avatar
    methods raise NotImplementedError — use Chanjing/Jogg for those.
    """

    provider_name = "google"

    def __init__(self, api_key: str):
        self._api_key = api_key or ""
        self._headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }

    # ── Avatar-video methods (not supported by Veo) ─────────────────

    async def list_avatars(self, page: int = 1, page_size: int = 50) -> list[Avatar]:
        return []

    async def list_voices(self, page: int = 1, page_size: int = 50) -> list[Voice]:
        return []

    async def create_avatar_video(self, req: CreateVideoRequest) -> str:
        raise AppError(
            "VEO_NO_AVATAR",
            "Google Veo does not support talking avatars. Use Chanjing or Jogg.",
            400,
        )

    async def get_video_status(self, task_id: str) -> VideoStatus:
        # Delegate to poll_broll_clip and wrap
        r = await self.poll_broll_clip(task_id)
        return VideoStatus(
            task_id=task_id,
            status="completed" if r["status"] == "completed" else ("failed" if r["status"] == "failed" else "processing"),
            progress=100 if r["status"] == "completed" else None,
            video_url=(r["output_urls"] or [None])[0],
            cover_url=None,
            duration_seconds=None,
            error_message=r.get("error"),
            raw={},
        )

    async def synthesize_speech(self, voice_id: str, text: str) -> str:
        raise AppError("VEO_NO_TTS", "Google Veo does not offer TTS.", 400)

    # ── AI creation (B-roll) ────────────────────────────────────────

    async def upload_temp_file(self, file_bytes: bytes, filename: str, service: str = "ai_creation") -> str:
        """Veo accepts inline base64 images in the request — no upload step needed.

        Returns a data URI so the caller's ref_img_url path still works.
        The submit method will detect data URIs and inline them.
        """
        # Guess MIME type from filename
        ext = (filename.rsplit(".", 1)[-1] or "png").lower()
        mime = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
        }.get(ext, "image/png")
        b64 = base64.b64encode(file_bytes).decode("ascii")
        return f"data:{mime};base64,{b64}"

    async def submit_broll_clip(
        self,
        prompt: str,
        duration: int = 6,
        aspect_ratio: str = "9:16",
        model_code: str = "veo-3.1-generate-preview",
        *,
        style_references: list["StyleReference"] | None = None,
        first_frame: "FirstFrame | None" = None,
        last_frame: "LastFrame | None" = None,
    ) -> str:
        """Submit a Veo video generation task.

        Reference handling per Veo 3.x API capability:

          - ``style_references`` (Class A, soft) — Veo 3.x has no
            native style-reference channel for video generation. Drop
            silently. (When Veo adds reference_images later, route
            here without changing the service-layer call.)

          - ``first_frame`` (Class B, hard) — maps to
            ``instance.image.bytesBase64Encoded``. Veo treats this as
            the i2v anchor frame.

          - ``last_frame`` — Veo 3.x doesn't support end-frame
            anchoring; raise ``UnsupportedReferenceMode``.

        model_code: veo-3.1-generate-preview | veo-3.1-lite-generate-preview
        Returns operation name (task_id for polling).
        """
        from app.adapters.video.base import (
            FirstFrame,
            LastFrame,
            StyleReference,
            UnsupportedReferenceMode,
        )
        _ = (FirstFrame, LastFrame, StyleReference)

        if last_frame is not None:
            raise UnsupportedReferenceMode(
                f"google-veo/{model_code} does not support last_frame anchoring"
            )

        if not self._api_key:
            raise AppError("VEO_NO_API_KEY", "Google Veo requires a Gemini API key", 401)

        # Build instances[0]: text prompt + optional reference image
        instance: dict[str, Any] = {"prompt": prompt}
        if first_frame is not None:
            # Veo expects bytesBase64Encoded inline
            img_b64, mime = await _fetch_image_as_base64(first_frame.url)
            instance["image"] = {
                "bytesBase64Encoded": img_b64,
                "mimeType": mime,
            }
        # style_references intentionally dropped — Veo 3.x has no
        # native style-ref channel.

        payload = {
            "instances": [instance],
            "parameters": {
                "aspectRatio": _aspect_to_veo(aspect_ratio),
                "durationSeconds": _veo_duration(duration),
                "personGeneration": "allow_all",
                # ``numberOfVideos`` was here previously and is the
                # documented default-1 for older Veo models. Newer Veo
                # endpoints (3.1+) reject the field with HTTP 400
                # "numberOfVideos isn't supported by this model" — and
                # since omitting it has always meant the same default
                # of 1 video per call, removing it is forward-compatible
                # without changing legacy behavior. Verified against
                # production logs where 4-of-4 broll shots failed with
                # exactly this error.
            },
        }

        url = f"{VEO_BASE_URL}/models/{model_code}:predictLongRunning"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            if resp.status_code >= 400:
                raise AppError(
                    "VEO_SUBMIT_FAILED",
                    f"Veo submit returned {resp.status_code}: {resp.text[:300]}",
                    502,
                )
            data = resp.json()

        op_name = data.get("name") or ""
        if not op_name:
            raise AppError("VEO_SUBMIT_MALFORMED", f"Veo response missing operation name: {data}", 502)
        logger.info("Google Veo B-roll submitted: %s (prompt=%s)", op_name, prompt[:50])
        return op_name

    async def poll_broll_clip(self, task_id: str) -> dict:
        """Poll a Veo long-running operation.

        task_id is the operation name returned by submit_broll_clip.
        Returns: {status, output_urls, error}
        """
        if not self._api_key:
            raise AppError("VEO_NO_API_KEY", "Google Veo requires a Gemini API key", 401)

        # operation names are like "models/veo-3.1-generate-preview/operations/xxx"
        url = f"{VEO_BASE_URL}/{task_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers)
            if resp.status_code >= 400:
                return {
                    "status": "failed",
                    "output_urls": [],
                    "error": f"Veo poll {resp.status_code}: {resp.text[:200]}",
                }
            data = resp.json()

        if not data.get("done"):
            return {"status": "processing", "output_urls": [], "error": None}

        # done=True: check for error or response
        err = data.get("error")
        if err:
            return {
                "status": "failed",
                "output_urls": [],
                "error": err.get("message") or str(err),
            }

        response = data.get("response") or {}
        # Veo response format: response.generateVideoResponse.generatedSamples[].video.uri
        videos = (
            response.get("generateVideoResponse", {}).get("generatedSamples")
            or response.get("videos")  # alternate format
            or []
        )
        urls: list[str] = []
        for v in videos:
            uri = (v.get("video") or {}).get("uri") or v.get("uri") or ""
            if uri:
                # Veo URIs need the API key appended to be directly downloadable
                sep = "&" if "?" in uri else "?"
                urls.append(f"{uri}{sep}key={self._api_key}")

        if not urls:
            return {
                "status": "failed",
                "output_urls": [],
                "error": f"Veo completed but no video URL found: {response}",
            }
        return {"status": "completed", "output_urls": urls, "error": None}

    # ── Image generation (Gemini Image / Nano Banana) ──────────────
    #
    # Synchronous: generateContent returns base64 image inline (no polling).

    async def generate_image(
        self,
        prompt: str,
        model_code: str = "gemini-3-pro-image-preview",
        ref_img_urls: list[str] | None = None,
        aspect_ratio: str = "9:16",
        image_size: str = "1K",
    ) -> list[str]:
        """Generate image(s) via Gemini Image API. Returns list of data URIs.

        model_code: gemini-3-pro-image-preview | gemini-3.1-flash-image-preview |
                   gemini-2.5-flash-image
        ref_img_urls: Optional reference images (up to 14) for image-to-image editing.
        Returns: list of "data:image/png;base64,..." URIs (caller can save or display).
        """
        if not self._api_key:
            raise AppError("GEMINI_NO_API_KEY", "Gemini image generation requires an API key", 401)

        # Build contents: text + optional reference images
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for ref in (ref_img_urls or [])[:14]:
            img_b64, mime = await _fetch_image_as_base64(ref)
            parts.append({"inlineData": {"mimeType": mime, "data": img_b64}})

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "aspectRatio": aspect_ratio,
                    "imageSize": image_size,
                },
            },
        }

        url = f"{VEO_BASE_URL}/models/{model_code}:generateContent"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            if resp.status_code >= 400:
                raise AppError(
                    "GEMINI_IMAGE_FAILED",
                    f"Gemini image returned {resp.status_code}: {resp.text[:300]}",
                    502,
                )
            data = resp.json()

        # Extract inline images from response
        results: list[str] = []
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                    results.append(f"data:{mime};base64,{inline['data']}")
        if not results:
            raise AppError("GEMINI_IMAGE_EMPTY", f"No image in response: {data}", 502)
        logger.info("Gemini image generated: %d image(s) via %s", len(results), model_code)
        return results


async def _fetch_image_as_base64(url_or_data: str) -> tuple[str, str]:
    """Fetch an image URL and return (base64, mime_type).

    If input is already a data URI, decodes and re-extracts.
    """
    if url_or_data.startswith("data:"):
        # data:image/png;base64,XXXX
        header, b64 = url_or_data.split(",", 1)
        mime = header[5:].split(";")[0] or "image/png"
        return b64, mime
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url_or_data)
        resp.raise_for_status()
        mime = resp.headers.get("content-type", "image/png").split(";")[0].strip()
        b64 = base64.b64encode(resp.content).decode("ascii")
        return b64, mime
