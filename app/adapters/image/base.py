"""ImageProvider Protocol — common abstraction for image-generation providers.

Two flows:

  * ``generate_image`` — text-only prompt, no visual reference
    (``/v1/images/generations`` on OpenAI). Output is a synthetic plate
    that callers compose with PIL afterwards if they want chrome.

  * ``generate_with_references`` — prompt + 1..N visual references
    (``/v1/images/edits`` on OpenAI). Trusts the model to compose the
    final marketing artifact end-to-end (style + chrome + logo +
    optional QR). Replaces the PIL renderer when supported.

A provider that doesn't speak edits raises ``UnsupportedReferenceMode``
so the caller can fall back to the text-only path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ImageAspect = Literal[
    "1:1", "9:16", "3:4", "4:5",
    "16:9", "4:3", "3:2",
    # Wide landscape ratios used by article-cover platforms:
    # 1.91:1 — LinkedIn / Substack OG cards (1200×627)
    # 2.35:1 — 公众号 long horizontal title image
    # Providers don't natively render these; they route to the
    # closest supported landscape size (3:2 / 16:9) and the service
    # layer crops to the exact target before saving.
    "1.91:1", "2.35:1",
]


class UnsupportedReferenceMode(Exception):
    """Raised when ``generate_with_references`` is invoked on a provider
    or proxy that doesn't expose an image-input endpoint. The caller
    should fall back to ``generate_image`` plus a PIL composite."""


@dataclass(frozen=True)
class GenerateImageRequest:
    """Request to generate a single image.

    ``prompt`` is the full text the model conditions on — callers are
    expected to fold style summaries / brand guidance into this string
    before submission. The adapter should NOT silently augment.
    """

    prompt: str
    aspect_ratio: ImageAspect = "9:16"
    quality: Literal["standard", "high"] = "standard"


@dataclass(frozen=True)
class GenerateWithReferencesRequest:
    """Request the model to compose an image conditioned on visual references.

    ``references`` carries raw image bytes (PNG/JPEG); the order is
    informational only — providers may treat the first as the primary
    style anchor and subsequent images as supporting elements (logo,
    QR, optional product shot).
    """

    prompt: str
    references: list[bytes]
    aspect_ratio: ImageAspect = "9:16"
    quality: Literal["standard", "high"] = "standard"


@dataclass(frozen=True)
class GenerateImageResult:
    """The provider's output. ``image_bytes`` is the raw image content
    (PNG or JPEG, provider-determined). Callers persist it via the
    storage adapter and decide on URLs."""

    image_bytes: bytes
    mime_type: str  # "image/png" | "image/jpeg"
    width: int
    height: int
    raw: dict  # original provider payload, for debug


class ImageProvider(Protocol):
    """Common interface for image-generation providers.

    Implementations are NOT required to be thread-safe — instantiate
    per-request or wrap with a lock.
    """

    provider_name: str

    async def generate_image(self, req: GenerateImageRequest) -> GenerateImageResult:
        """Generate one image from a text prompt. Raises on provider error."""
        ...

    async def generate_with_references(
        self, req: GenerateWithReferencesRequest
    ) -> GenerateImageResult:
        """Generate one image conditioned on visual references.

        Raises ``UnsupportedReferenceMode`` when the underlying provider /
        proxy doesn't expose an image-input endpoint — callers should
        fall back to ``generate_image``.
        """
        ...
