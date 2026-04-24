from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AspectRatio = Literal["portrait", "landscape", "square"]
JobStatus = Literal["pending", "processing", "completed", "failed"]


class VideoGenerateRequest(BaseModel):
    """Body of POST /api/v1/creations/{cid}/videos."""

    provider_config_id: str
    avatar_id: str
    voice_id: str
    script: str = Field(..., min_length=1, max_length=4000)
    aspect_ratio: AspectRatio = "portrait"
    caption: bool = True
    subtitle_style: str = "classic"  # classic|bold|minimal
    subtitle_color: str | None = None   # custom font color override, e.g. "#FFD700"
    subtitle_stroke: str | None = None  # custom stroke color override, e.g. "#000000"
    broll: bool = False  # auto-generate B-roll from visual_direction
    # Optional override for the B-roll plan. When ``None`` the service uses
    # the AI-director's plan stored on creation.structured_content.broll_plan.
    # Set only when the user edited/added B-roll entries in the UI — each
    # entry matches the schema emitted by script_composer:
    #   {"type": "retention"|"illustrative", "insert_after_char": int,
    #    "duration_seconds": int, "prompt": str}
    broll_plan: list[dict] | None = None
    name: str | None = None  # used by Jogg only
    # Opaque provider-specific hints captured from the avatar/voice list payload
    # (e.g. Chanjing requires `{"figure_type": "sit_body"|...}`).
    provider_extras: dict = Field(default_factory=dict)


class VideoJobResponse(BaseModel):
    id: str
    creation_id: str
    provider: str
    provider_config_id: str | None
    provider_task_id: str | None
    status: JobStatus
    params: dict
    video_url: str | None
    cover_url: str | None
    duration_seconds: int | None
    progress: int | None
    error_message: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str
    updated_at: str


class VideoJobWithCreationResponse(VideoJobResponse):
    """Video job enriched with parent creation context for the global Video
    Studio view, where users browse videos across creations."""

    creation_title: str
    creation_content_type: str
