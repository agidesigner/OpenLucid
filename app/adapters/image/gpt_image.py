"""OpenAI gpt-image-1 adapter.

The OpenAI Images endpoint returns base64-encoded image data inline (no
URL polling needed), so generation is a single synchronous HTTP call.

Aspect-ratio handling: gpt-image-1 supports three sizes — 1024×1024,
1024×1536 (portrait 2:3), 1536×1024 (landscape 3:2). We map our public
aspect labels onto the closest supported size and let the renderer pad/
crop downstream when a perfect match isn't available.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

from app.adapters.image.base import (
    GenerateImageRequest,
    GenerateImageResult,
    GenerateWithReferencesRequest,
    ImageAspect,
    UnsupportedReferenceMode,
)
from app.exceptions import AppError

logger = logging.getLogger(__name__)


_SIZE_MAP: dict[ImageAspect, tuple[str, int, int]] = {
    "1:1":    ("1024x1024", 1024, 1024),
    "9:16":   ("1024x1536", 1024, 1536),
    "3:4":    ("1024x1536", 1024, 1536),
    "4:5":    ("1024x1536", 1024, 1536),
    "16:9":   ("1536x1024", 1536, 1024),
    "4:3":    ("1536x1024", 1536, 1024),
    "3:2":    ("1536x1024", 1536, 1024),
    # Wide landscape covers — provider only renders up to 3:2, so
    # both fall through to 1536×1024 here. The service layer crops
    # to the exact target (1.91:1 ≈ 1536×803, 2.35:1 ≈ 1536×654)
    # before saving, so users get what they asked for, not a
    # silently-narrower 16:9-ish image.
    "1.91:1": ("1536x1024", 1536, 1024),
    "2.35:1": ("1536x1024", 1536, 1024),
}


def _sniff_image_format(raw: bytes) -> tuple[str, str]:
    """Sniff image bytes → (filename_ext, mime_type).

    The service-layer compressor re-encodes large reference posters as
    JPEG, but the adapter previously labeled every part as PNG. Strict
    proxies / OpenAI's edits endpoint can reject mismatched MIME, so
    we look at the magic bytes instead of trusting any caller-supplied
    label. Default to PNG for unknown bytes — the gpt-image endpoint
    accepts PNG for almost anything reasonable.
    """
    if not raw:
        return "png", "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if raw.startswith(b"RIFF") and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "png", "image/png"


class GPTImageProvider:
    """OpenAI gpt-image-1 image-generation adapter."""

    provider_name = "openai_image"

    def __init__(self, api_key: str, base_url: str | None = None, model: str = "gpt-image-1"):
        from openai import AsyncOpenAI

        if not api_key:
            raise AppError("PROVIDER_NOT_CONFIGURED", "OpenAI API key is missing", 400)
        # 300s read timeout — gpt-image-2 high-quality renders take 2-3min
        # via proxies that gateway through additional inference layers.
        # Default is far too short and makes those calls look like outages.
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or "https://api.openai.com/v1",
            timeout=300.0,
        )
        self.model = model
        # Cached list of image-capable models on the proxy. Populated
        # lazily on the first error-path probe so we don't pay an
        # extra round-trip on the happy path. Kept on the instance —
        # adapter rebuilt per request, so cache lifetime is naturally
        # bounded; we just want one probe per failed request, not one
        # per call site within the same generation flow.
        self._proxy_image_models_cache: list[str] | None = None

    async def _list_proxy_image_models(self) -> list[str]:
        """Probe ``/v1/models`` and filter to image-capable names.

        Returns ``[]`` when the probe fails OR when the proxy advertises
        no image-related models. Cached on the instance after the first
        successful probe.
        """
        if self._proxy_image_models_cache is not None:
            return self._proxy_image_models_cache
        IMAGE_KEYWORDS = ("image", "dall", "sd", "flux", "stable-diffusion")
        try:
            page = await self.client.models.list()
            names = [m.id for m in page.data]
            img = sorted({n for n in names if any(k in n.lower() for k in IMAGE_KEYWORDS)})
            self._proxy_image_models_cache = img
            return img
        except Exception as e:
            logger.debug("models.list() probe failed: %s", e)
            self._proxy_image_models_cache = []
            return []

    @staticmethod
    def _looks_like_model_not_found(blob: str) -> bool:
        """Detect ``model_not_found`` in either OpenAI canonical errors
        or the user's localized proxy variant.

        OpenAI / OpenAI-compatible proxies surface this as either:
          - ``code: model_not_found`` (canonical)
          - ``model … does not exist`` (older OpenAI prose)
          - ``模型 … 不存在`` (the user's Chinese-localized proxy)
        """
        s = (blob or "")
        s_lower = s.lower()
        if "model_not_found" in s_lower:
            return True
        if "model" in s_lower and ("does not exist" in s_lower or "not found" in s_lower):
            return True
        if "不存在" in s and "模型" in s:
            return True
        return False

    async def _humanize_image_error(self, raw: str) -> str:
        """Convert raw upstream error text into an actionable message.

        Only special-cases ``model_not_found``. Everything else falls
        through with a short prefix so the original signal (timeout,
        auth, rate limit, content policy) survives.
        """
        if self._looks_like_model_not_found(raw):
            available = await self._list_proxy_image_models()
            if available:
                avail_list = ", ".join(f"`{n}`" for n in available[:5])
                more = f"（共 {len(available)} 个）" if len(available) > 5 else ""
                return (
                    f"你配置的代理里没有模型 `{self.model}`。"
                    f"代理实际支持的图像模型：{avail_list}{more}。"
                    f"请去 设置 → 媒体能力 → 图像生成 切换 model。"
                )
            return (
                f"你配置的代理里没有模型 `{self.model}`，"
                f"且代理未列出任何图像模型。请在代理那边确认 image API "
                f"是否安装，或在 设置 → 媒体能力 切换到其他 provider（如蝉镜）。"
            )
        return f"OpenAI image generation failed: {raw[:300]}"

    async def generate_image(self, req: GenerateImageRequest) -> GenerateImageResult:
        size, width, height = _SIZE_MAP.get(req.aspect_ratio, _SIZE_MAP["9:16"])
        # gpt-image-1's "quality" param: low|medium|high (we map standard→medium)
        api_quality = "high" if req.quality == "high" else "medium"

        try:
            resp = await self.client.images.generate(
                model=self.model,
                prompt=req.prompt,
                size=size,
                quality=api_quality,
                n=1,
            )
        except Exception as e:
            # Surface the OpenAI error verbatim — it usually carries
            # a precise reason ("content policy violation", "invalid
            # api_key", etc.) that the user can act on.
            logger.error("gpt-image-1 request failed: %s", e)
            detail = await self._humanize_image_error(str(e))
            raise AppError("IMAGE_PROVIDER_ERROR", detail, 502) from e

        if not resp.data:
            raise AppError(
                "IMAGE_PROVIDER_EMPTY",
                "OpenAI returned an empty image response",
                502,
            )

        item = resp.data[0]
        b64 = getattr(item, "b64_json", None)
        if not b64:
            # Some response shapes return a URL instead — fetch it.
            url = getattr(item, "url", None)
            if not url:
                raise AppError(
                    "IMAGE_PROVIDER_EMPTY",
                    "OpenAI image response missing both b64_json and url",
                    502,
                )
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                got = await client.get(url)
                got.raise_for_status()
                image_bytes = got.content
        else:
            image_bytes = base64.b64decode(b64)

        # Detect actual dimensions from the bytes — the model may return
        # a slightly different size than requested if it auto-cropped.
        try:
            from PIL import Image

            with Image.open(BytesIO(image_bytes)) as img:
                actual_w, actual_h = img.size
                fmt = (img.format or "").lower()
        except Exception:
            actual_w, actual_h, fmt = width, height, "png"

        mime = "image/png" if fmt != "jpeg" else "image/jpeg"
        return GenerateImageResult(
            image_bytes=image_bytes,
            mime_type=mime,
            width=actual_w,
            height=actual_h,
            raw={"model": self.model, "size": size, "quality": api_quality},
        )

    async def generate_with_references(
        self, req: GenerateWithReferencesRequest
    ) -> GenerateImageResult:
        """OpenAI ``/v1/images/edits`` with image[] reference inputs.

        The Images Edits endpoint takes one or more reference images and
        a prompt; the model conditions visually on the references plus
        textually on the prompt. This is what lets us trust the model
        with end-to-end poster composition (style + headline rendering +
        logo placement) instead of doing PIL composition.

        Raises ``UnsupportedReferenceMode`` when the proxy / endpoint
        rejects the call so callers can fall back. Distinguishing
        "endpoint missing" from a transient network blip matters: we
        treat 404, 400 with model_not_found, and SSL handshake failures
        all as missing endpoint, but pass through transient timeouts as
        ordinary errors.
        """
        if not req.references:
            raise AppError(
                "NO_REFERENCES",
                "generate_with_references requires at least one reference image",
                400,
            )

        size, width, height = _SIZE_MAP.get(req.aspect_ratio, _SIZE_MAP["9:16"])
        api_quality = "high" if req.quality == "high" else "medium"

        # Hand-roll the multipart upload via httpx because the OpenAI
        # Python SDK's ``client.images.edit(image=...)`` doesn't accept a
        # list of bytes — it wants a single file handle. The wire format
        # for multi-image inputs is **repeated** ``image`` fields (NOT
        # ``image[]``). Some proxies tolerate ``image[]`` but the canonical
        # OpenAI endpoint will reject it with 400. See:
        # https://platform.openai.com/docs/api-reference/images/createEdit
        import httpx

        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for idx, raw in enumerate(req.references):
            ext, mime = _sniff_image_format(raw)
            files.append(
                ("image", (f"ref{idx}.{ext}", raw, mime))
            )

        data = {
            "model": self.model,
            "prompt": req.prompt,
            "size": size,
            "quality": api_quality,
            "n": "1",
        }

        # Pull the configured base_url + api_key out of the SDK client so
        # we hit the same proxy / credentials.
        base_url = str(self.client.base_url).rstrip("/")
        url = f"{base_url}/images/edits"
        headers = {
            "Authorization": f"Bearer {self.client.api_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, headers=headers, data=data, files=files)
        except httpx.ConnectError as e:
            # Proxies that don't expose /images/edits typically reject
            # the TLS handshake during connection setup — treat as
            # missing endpoint, not a network glitch.
            logger.warning("/images/edits connect failed: %s", e)
            raise UnsupportedReferenceMode(
                "Provider proxy does not expose /v1/images/edits"
            ) from e
        except httpx.ReadError as e:
            logger.warning("/images/edits read failed: %s", e)
            raise UnsupportedReferenceMode(
                "Provider proxy refused image-edits payload"
            ) from e

        if resp.status_code == 404:
            raise UnsupportedReferenceMode(
                "Provider proxy returned 404 for /v1/images/edits"
            )
        if resp.status_code == 400:
            body = resp.text
            if self._looks_like_model_not_found(body):
                # Two distinct causes share this upstream message:
                #   (A) The model isn't on the proxy at all — the user
                #       has the wrong model name in Settings and needs
                #       to switch.
                #   (B) The model exists on the proxy generally (it
                #       answers /v1/chat etc.) but /v1/images/edits
                #       can't serve it — caller should fall back to
                #       /v1/images/generations text-only.
                # Disambiguate via the cached models.list() probe:
                # if the model is genuinely missing, surface the
                # humanized "switch model" guidance; otherwise keep
                # the legacy fallback so proxies that simply lack
                # the edits endpoint still work.
                available = await self._list_proxy_image_models()
                if available and self.model not in available:
                    detail = await self._humanize_image_error(body)
                    raise AppError("IMAGE_PROVIDER_ERROR", detail, 502)
                raise UnsupportedReferenceMode(
                    f"Model {self.model!r} unavailable on /v1/images/edits"
                )
            raise AppError("IMAGE_PROVIDER_ERROR", f"OpenAI 400: {body[:300]}", 502)
        if resp.status_code >= 500:
            # Some proxies return 5xx for model_not_found instead of the
            # canonical 400 (the user's deployment does — Error code: 503
            # with body code='model_not_found'). Run the same humanizer
            # so users get an actionable message instead of a raw 5xx.
            detail = await self._humanize_image_error(resp.text)
            raise AppError("IMAGE_PROVIDER_ERROR", detail, 502)
        if resp.status_code != 200:
            detail = await self._humanize_image_error(resp.text)
            raise AppError("IMAGE_PROVIDER_ERROR", detail, 502)

        try:
            payload = resp.json()
        except Exception as e:
            raise AppError(
                "IMAGE_PROVIDER_ERROR",
                f"OpenAI returned non-JSON: {resp.text[:300]}",
                502,
            ) from e

        items = payload.get("data") or []
        if not items:
            raise AppError(
                "IMAGE_PROVIDER_EMPTY", "OpenAI returned no image", 502
            )
        item = items[0]
        b64 = item.get("b64_json")
        if not b64:
            raise AppError(
                "IMAGE_PROVIDER_EMPTY",
                "OpenAI image-edits response missing b64_json",
                502,
            )
        image_bytes = base64.b64decode(b64)

        try:
            from PIL import Image as _PILImage

            with _PILImage.open(BytesIO(image_bytes)) as img:
                actual_w, actual_h = img.size
                fmt = (img.format or "").lower()
        except Exception:
            actual_w, actual_h, fmt = width, height, "png"

        mime = "image/png" if fmt != "jpeg" else "image/jpeg"
        return GenerateImageResult(
            image_bytes=image_bytes,
            mime_type=mime,
            width=actual_w,
            height=actual_h,
            raw={
                "model": self.model,
                "size": size,
                "quality": api_quality,
                "endpoint": "edits",
                "reference_count": len(req.references),
            },
        )
