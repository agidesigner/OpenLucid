"""ChanjingImageProvider — image generation via chanjing's AI creation API.

Chanjing serves both video and image gen through the same endpoint
(``/open/v1/ai_creation/task/submit`` + ``/open/v1/ai_creation/task``);
the discriminator is ``creation_type`` (3 = image, 4 = video) plus the
``model_code``. Our adapter reuses the existing ChanjingVideoProvider
for the HTTP plumbing (signature, error decoding, temp-file upload)
and folds the image-specific payload + poll loop on top.

Submit returns a ``unique_id``; the call is async — we poll until the
task succeeds, then download the resulting image URL into bytes for
the ImageProvider Protocol.
"""

from __future__ import annotations

import asyncio
import logging
import time
from io import BytesIO

import httpx

from app.adapters.image.base import (
    GenerateImageRequest,
    GenerateImageResult,
    GenerateWithReferencesRequest,
    UnsupportedReferenceMode,
)
from app.adapters.video.chanjing import ChanjingVideoProvider
from app.exceptions import AppError

logger = logging.getLogger(__name__)


# Chanjing's accepted aspect strings — image variant. Same set as video
# for Doubao/Kling, but we keep the map explicit so a future Wan-only
# limitation doesn't silently fall back to a wrong value.
_ASPECT_MAP: dict[str, str] = {
    "1:1": "1:1",
    "9:16": "9:16",
    "3:4": "3:4",
    "4:5": "3:4",  # nearest portrait
    "16:9": "16:9",
    "4:3": "4:3",
    "3:2": "16:9",  # nearest landscape
}

# Per-model default clarity (longest side in pixels). Seedream 4.5 is the
# only one that natively supports 4K; the others do best at 2048.
_CLARITY_DEFAULT: dict[str, int] = {
    "doubao-seedream-4.5": 2048,
    "doubao-seedream-4.0": 2048,
    "doubao-seedream-3.0": 2048,
    "doubao-seedream-3.0-t2i": 2048,
    "kling-v2-1": 2048,
    "kling-v2": 2048,
    "wan2.2-t2i": 2048,
}

# Models that don't accept reference images on chanjing's image endpoint.
# Currently Seedream 3.0 is the only "no ref" image model in the registry;
# everything else (including all Kling versions) accepts ``ref_img_url``.
_REF_UNSUPPORTED_MARKERS = ("seedream-3.0", "wan2.2",)

# Polling cadence — chanjing image tasks land in 20-90s typically. Cap
# at 5 minutes so a stuck job doesn't hang the request indefinitely.
_POLL_INTERVAL_S = 4.0
_POLL_TIMEOUT_S = 300.0


class ChanjingImageProvider:
    """Image-generation adapter for chanjing's AI creation API."""

    provider_name = "chanjing_image"

    def __init__(self, app_id: str, secret_key: str, model: str = "doubao-seedream-4.5"):
        if not app_id or not secret_key:
            raise AppError(
                "PROVIDER_NOT_CONFIGURED",
                "Chanjing image generation requires app_id + secret_key",
                400,
            )
        # Reuse the existing video adapter for HMAC signing, error
        # mapping, and temp-file upload helpers.
        self._chanjing = ChanjingVideoProvider(app_id=app_id, secret_key=secret_key)
        self.model = model

    async def generate_image(
        self, req: GenerateImageRequest
    ) -> GenerateImageResult:
        return await self._submit_and_wait(
            prompt=req.prompt,
            aspect=req.aspect_ratio,
            ref_urls=[],
        )

    async def generate_with_references(
        self, req: GenerateWithReferencesRequest
    ) -> GenerateImageResult:
        if not req.references:
            raise AppError(
                "NO_REFERENCES",
                "generate_with_references requires at least one reference image",
                400,
            )
        # Older Doubao image models don't accept ref_img_url on the
        # image endpoint — bail loudly rather than silently dropping
        # references the user explicitly provided.
        lower_model = self.model.lower()
        if any(marker in lower_model for marker in _REF_UNSUPPORTED_MARKERS):
            raise UnsupportedReferenceMode(
                f"chanjing/{self.model} does not accept reference images on the image endpoint. "
                f"Pick Seedream 4.0 / 4.5 / Kling v2 / v2.1 for image-to-image."
            )

        # Upload each reference to chanjing's temp storage so we can
        # pass URLs to the submit call. Up to 4 refs to keep the
        # multipart cost reasonable.
        ref_urls: list[str] = []
        for idx, raw in enumerate(req.references[:4]):
            ext, _mime = _sniff_ext(raw)
            url = await self._chanjing.upload_temp_file(
                file_bytes=raw,
                filename=f"ref{idx}.{ext}",
                service="ai_creation",
            )
            ref_urls.append(url)

        return await self._submit_and_wait(
            prompt=req.prompt,
            aspect=req.aspect_ratio,
            ref_urls=ref_urls,
        )

    async def _submit_and_wait(
        self,
        *,
        prompt: str,
        aspect: str,
        ref_urls: list[str],
    ) -> GenerateImageResult:
        chanjing_aspect = _ASPECT_MAP.get(aspect, "9:16")
        clarity = _CLARITY_DEFAULT.get(self.model, 2048)

        # Chanjing's image endpoint requires ``number_of_images`` (1-4).
        # We hard-code 1 — the service consumes a single output and we
        # don't want to pay for + then discard 3 extras. ``quality_mode``
        # is video-only; including it on image submits causes a silent
        # parameter mismatch on some model_codes, so we leave it off.
        # Field name verified against:
        # https://doc.chanjing.cc/api/ai-creation/pic-seedream-4.5.html
        payload = {
            "ref_prompt": prompt,
            "creation_type": 3,  # image task
            "model_code": self.model,
            "aspect_ratio": chanjing_aspect,
            "clarity": clarity,
            "number_of_images": 1,
        }
        if ref_urls:
            payload["ref_img_url"] = ref_urls

        try:
            body = await self._chanjing._request(  # type: ignore[attr-defined]
                "POST",
                "/open/v1/ai_creation/task/submit",
                json_body=payload,
            )
        except AppError as e:
            if "模型不存在" in str(e):
                raise AppError(
                    "CHANJING_IMAGE_SUBMIT_FAILED",
                    (
                        f"chanjing rejected model_code={self.model!r}. "
                        "Each Seedream/Kling/Wan version has its own model_code "
                        "string — verify against https://doc.chanjing.cc/api/ai-creation/"
                    ),
                    502,
                ) from e
            raise

        unique_id = body.get("data")
        if not unique_id:
            raise AppError(
                "CHANJING_IMAGE_SUBMIT_FAILED",
                f"AI creation submit returned unexpected data: {body}",
                502,
            )
        logger.info(
            "Chanjing image submitted: %s (model=%s, prompt=%s)",
            unique_id,
            self.model,
            prompt[:50],
        )

        # Poll until terminal.
        deadline = time.monotonic() + _POLL_TIMEOUT_S
        last_err: str | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)
            status = await self._chanjing.poll_broll_clip(str(unique_id))
            if status["status"] == "completed":
                output_urls = status.get("output_urls") or []
                if not output_urls:
                    raise AppError(
                        "CHANJING_IMAGE_EMPTY",
                        f"chanjing task {unique_id} completed but returned no output URLs",
                        502,
                    )
                # Download the first output to bytes.
                image_url = output_urls[0]
                async with httpx.AsyncClient(timeout=60) as http:
                    resp = await http.get(image_url)
                    resp.raise_for_status()
                    image_bytes = resp.content
                width, height, mime = _detect_image_meta(image_bytes)
                return GenerateImageResult(
                    image_bytes=image_bytes,
                    mime_type=mime,
                    width=width,
                    height=height,
                    raw={
                        "model": self.model,
                        "unique_id": str(unique_id),
                        "endpoint": "chanjing_ai_creation",
                        "reference_count": len(ref_urls),
                        "output_url": image_url,
                    },
                )
            if status["status"] == "failed":
                last_err = status.get("error") or "unknown failure"
                raise AppError(
                    "CHANJING_IMAGE_FAILED",
                    f"chanjing task {unique_id} failed: {last_err}",
                    502,
                )
            # else: processing — keep polling.

        raise AppError(
            "CHANJING_IMAGE_TIMEOUT",
            f"chanjing task {unique_id} did not complete within "
            f"{_POLL_TIMEOUT_S}s (last status: {last_err or 'processing'})",
            504,
        )


def _sniff_ext(raw: bytes) -> tuple[str, str]:
    """Return (ext, mime) for chanjing's temp upload — chanjing infers
    Content-Type from the URL extension, so the right ext matters."""
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "png", "image/png"


def _detect_image_meta(raw: bytes) -> tuple[int, int, str]:
    try:
        from PIL import Image as _PILImage

        with _PILImage.open(BytesIO(raw)) as img:
            w, h = img.size
            fmt = (img.format or "").lower()
    except Exception:
        return 0, 0, "image/png"
    mime = {
        "jpeg": "image/jpeg",
        "jpg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(fmt, "image/png")
    return w, h, mime
