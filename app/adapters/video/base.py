"""VideoProvider Protocol — common abstraction for talking-avatar-video providers.

This is the maximum-common-denominator interface across providers like Chanjing
and Jogg.ai. Provider-specific extras live in their own adapter modules and are
not exposed via this Protocol.

Field-level alignment between Chanjing and Jogg has been verified at the
endpoint-doc level — see /Users/ajin/.claude/plans/composed-wobbling-stroustrup.md
for the alignment table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

AspectRatio = Literal["portrait", "landscape", "square"]
JobStatus = Literal["pending", "processing", "completed", "failed"]


# ── Reference-image domain types ─────────────────────────────────────
#
# Two distinct semantic categories of "reference image" — confusing them
# is what produced the chanjing 50000 "aspect ratio out of range" bug.
#
#   StyleReference  — Class A. "Make the video look like this." Soft.
#                     Auto-sourced from the offer's KB by default.
#                     Provider may DROP this silently if it has no
#                     native style-reference field (chanjing/Doubao,
#                     Veo as of Apr 2026), and optionally fall back to
#                     prompt augmentation if ``description`` is set.
#                     Aspect ratio is loose; no hard validation.
#
#   FirstFrame      — Class B-start. "Video begins exactly with this
#                     frame." Hard. User explicitly uploads in the UI
#                     for this purpose; never auto-sourced from KB.
#                     Provider MUST raise UnsupportedReferenceMode if
#                     it cannot honor this — silently dropping would
#                     violate the user's explicit intent. Aspect ratio
#                     must match the output video's aspect.
#
#   LastFrame       — Class B-end. Mirror of FirstFrame.
#
# Why this split: a StyleReference being silently dropped on chanjing
# is the right behavior (user wanted a hint, the model couldn't take
# it — no harm done). A FirstFrame being silently dropped on a model
# that doesn't support i2v would mean the user uploaded a precise
# starting frame and got back a video that ignored it — silent, hidden
# product bug. The two semantics CANNOT share a single ``ref_img_url``
# parameter; that's what we had before and it's why chanjing's
# ref_img_url (a Class B field per its API contract) was being fed
# Class A images from the offer KB.


@dataclass(frozen=True)
class StyleReference:
    """Soft visual anchor — 'make it LOOK like this'.

    Provider may drop this silently if unsupported. The optional
    ``description`` lets the provider fall back to text-prompt
    augmentation when native style-ref isn't available.
    """
    url: str
    description: str | None = None
    weight: float = 1.0  # relative importance when multiple are passed


@dataclass(frozen=True)
class FirstFrame:
    """Hard temporal anchor — 'video starts exactly with this frame'.

    Provider MUST raise on unsupported. Aspect must match output video."""
    url: str


@dataclass(frozen=True)
class LastFrame:
    """Hard temporal anchor — 'video ends exactly on this frame'.

    Provider MUST raise on unsupported. Aspect must match output video."""
    url: str


class UnsupportedReferenceMode(Exception):
    """Raised when a provider receives a hard reference (FirstFrame /
    LastFrame) it cannot honor. Should bubble up to the user as a
    config error, not a silent ignore. StyleReference does NOT raise
    this — it's soft."""


@dataclass
class Avatar:
    """A digital avatar/person that can speak a script.

    `gender` is normalized to "male" | "female" | None across providers
    (Chanjing 男/女 → male/female, Jogg male/female → male/female).

    `age` is normalized to "young" | "adult" | "senior" | None.

    `extras` is a provider-specific hint dict — opaque to callers but expected
    to be echoed back via `CreateVideoRequest.provider_extras` so the adapter
    can supply provider-specific fields it needs at create time. For Chanjing
    this carries `{"figure_type": "sit_body" | "circle_view" | ..., "paired_voice_id": "..."}`;
    Jogg leaves it empty.
    """

    id: str
    name: str
    gender: str | None  # "male" | "female" | None — normalized
    preview_image_url: str
    preview_video_url: str | None = None
    age: str | None = None  # "young" | "adult" | "senior" | None — normalized
    extras: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)  # original provider payload, for debug


@dataclass
class Voice:
    """A TTS voice (audio_man / voice_id).

    `gender` and `age` follow the same normalization as Avatar.
    """

    id: str
    name: str
    gender: str | None  # "male" | "female" | None — normalized
    language: str | None
    sample_url: str
    age: str | None = None  # "young" | "adult" | "senior" | None — normalized
    extras: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class CreateVideoRequest:
    """Request to create a talking-avatar video.

    `script` must be <= 4000 characters (Chanjing hard limit; Jogg also reasonable).

    `provider_extras` is an opaque per-provider hint dict for things that don't
    fit the cross-provider Protocol, e.g. Chanjing requires `figure_type` (one of
    "sit_body" / "circle_view" / "whole_body" / ...) which is specific to which
    figure variant of the public avatar to use. The frontend captures this from
    the avatar list payload and echoes it back here.
    """

    avatar_id: str
    voice_id: str
    script: str
    aspect_ratio: AspectRatio = "portrait"
    caption: bool = True
    subtitle_style: str = "classic"  # classic|bold|minimal
    subtitle_color: str | None = None   # custom font color override
    subtitle_stroke: str | None = None  # custom stroke color override
    broll: bool = False  # auto-generate B-roll from visual_direction
    name: str | None = None  # Jogg only — Chanjing ignores
    provider_extras: dict = field(default_factory=dict)


@dataclass
class VideoStatus:
    """Status snapshot of a video generation task."""

    task_id: str
    status: JobStatus
    progress: int | None  # 0-100, None if provider doesn't expose
    video_url: str | None
    cover_url: str | None
    duration_seconds: int | None
    error_message: str | None
    raw: dict = field(default_factory=dict)


class VideoProvider(Protocol):
    """Common interface for talking-avatar-video providers.

    Implementations are NOT required to be thread-safe — instantiate per-request
    or wrap with a lock.
    """

    provider_name: str

    async def list_avatars(self, page: int = 1, page_size: int = 50) -> list[Avatar]:
        """Return public avatars available to this account."""
        ...

    async def list_voices(self, page: int = 1, page_size: int = 50) -> list[Voice]:
        """Return public voices/timbres available to this account."""
        ...

    async def create_avatar_video(self, req: CreateVideoRequest) -> str:
        """Submit an async video generation task. Returns provider-side task_id."""
        ...

    async def get_video_status(self, task_id: str) -> VideoStatus:
        """Poll the status of a previously created task."""
        ...

    async def synthesize_speech(self, voice_id: str, text: str) -> str:
        """TTS-synthesize the given text in the given voice. Returns audio URL.

        Used by the audition button in the UI to let the user hear how their
        actual script sounds in each voice. Providers without a standalone TTS
        endpoint (e.g. Jogg) should fall back to returning the voice's
        pre-recorded sample URL.
        """
        ...
