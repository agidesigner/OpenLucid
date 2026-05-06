"""GoogleImageProvider — thin wrapper around GoogleVeoProvider.generate_image.

Gemini Nano Banana (gemini-{2.5,3,3.1}-*-image-preview) is a single-call
synchronous endpoint: prompt + optional reference images → list of
inline base64 PNGs in the response. The heavy lifting (HTTP plumbing,
auth headers, multi-image input encoding, error mapping) already lives
in ``app.adapters.video.google_veo.GoogleVeoProvider`` because the same
Google API key drives Veo (video) and Gemini Image (image). We just
adapt the return type to our ``ImageProvider`` Protocol.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

from app.adapters.image.base import (
    GenerateImageRequest,
    GenerateImageResult,
    GenerateWithReferencesRequest,
    UnsupportedReferenceMode,
)
from app.adapters.video.google_veo import GoogleVeoProvider
from app.exceptions import AppError

logger = logging.getLogger(__name__)


# Map our public aspect labels to Gemini's accepted values. Gemini
# documents "1:1 / 3:4 / 4:3 / 9:16 / 16:9" — the rest fall through to
# the closest legal value so callers don't get a 400 for asking 4:5.
_ASPECT_MAP: dict[str, str] = {
    "1:1": "1:1",
    "9:16": "9:16",
    "3:4": "3:4",
    "4:3": "4:3",
    "4:5": "3:4",   # closest portrait
    "16:9": "16:9",
    "3:2": "16:9",  # closest landscape
}


class GoogleImageProvider:
    """Image-generation adapter for Gemini Nano Banana."""

    provider_name = "google_image"

    def __init__(self, api_key: str, model: str = "gemini-3-pro-image-preview"):
        if not api_key:
            raise AppError(
                "PROVIDER_NOT_CONFIGURED",
                "Google Gemini image generation requires an API key",
                400,
            )
        self._veo = GoogleVeoProvider(api_key=api_key)
        self.model = model

    async def generate_image(
        self, req: GenerateImageRequest
    ) -> GenerateImageResult:
        return await self._call(req.prompt, req.aspect_ratio, ref_urls=None)

    async def generate_with_references(
        self, req: GenerateWithReferencesRequest
    ) -> GenerateImageResult:
        # Gemini accepts up to 14 reference images; we cap at 8 to leave
        # headroom and stay close to the multipart cap of the OpenAI
        # adapter so output style is comparable across providers.
        if not req.references:
            raise AppError(
                "NO_REFERENCES",
                "generate_with_references requires at least one reference image",
                400,
            )
        ref_data_uris = [_bytes_to_data_uri(b) for b in req.references[:8]]
        return await self._call(req.prompt, req.aspect_ratio, ref_urls=ref_data_uris)

    async def _call(
        self,
        prompt: str,
        aspect: str,
        *,
        ref_urls: list[str] | None,
    ) -> GenerateImageResult:
        gemini_aspect = _ASPECT_MAP.get(aspect, "9:16")
        try:
            data_uris = await self._veo.generate_image(
                prompt=prompt,
                model_code=self.model,
                ref_img_urls=ref_urls,
                aspect_ratio=gemini_aspect,
                image_size="2K",
            )
        except AppError:
            raise
        except Exception as e:
            raise AppError(
                "IMAGE_PROVIDER_ERROR",
                f"Gemini image generation failed: {e}",
                502,
            ) from e

        if not data_uris:
            raise AppError(
                "IMAGE_PROVIDER_EMPTY",
                "Gemini returned no image data",
                502,
            )

        # Take the first result; google_veo currently always returns 1
        # but the Protocol leaves multi-image expansion as future work.
        first = data_uris[0]
        if not first.startswith("data:"):
            raise AppError(
                "IMAGE_PROVIDER_ERROR",
                f"Unexpected Gemini response format: {first[:60]}",
                502,
            )
        header, b64 = first.split(",", 1)
        mime = header[5:].split(";")[0] or "image/png"
        image_bytes = base64.b64decode(b64)

        # Detect actual dimensions for transparency in the job audit.
        try:
            from PIL import Image as _PILImage

            with _PILImage.open(BytesIO(image_bytes)) as img:
                actual_w, actual_h = img.size
        except Exception:
            actual_w, actual_h = 0, 0

        return GenerateImageResult(
            image_bytes=image_bytes,
            mime_type=mime,
            width=actual_w,
            height=actual_h,
            raw={
                "model": self.model,
                "aspect": gemini_aspect,
                "endpoint": "gemini_generate_content",
                "reference_count": len(ref_urls or []),
            },
        )


def _bytes_to_data_uri(raw: bytes) -> str:
    """Encode raw image bytes as a data URI Gemini can consume.

    Gemini's ``inlineData`` field accepts both PNG and JPEG. Sniff the
    leading magic bytes so a JPEG-shrunk poster doesn't get mislabeled
    as PNG (the same pitfall fixed in the OpenAI adapter).
    """
    if not raw:
        raise AppError("EMPTY_REFERENCE", "Reference image bytes are empty", 400)
    mime = "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
