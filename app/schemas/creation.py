import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreationBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=1)
    content_type: str = Field("general", max_length=50)
    tags: list[str] | None = None
    source_note: str | None = None


class CreationCreate(CreationBase):
    merchant_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None
    source_app: str = Field("manual", max_length=80)


class CreationUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=512)
    content: str | None = Field(None, min_length=1)
    content_type: str | None = Field(None, max_length=50)
    tags: list[str] | None = None
    source_note: str | None = None


class CreationVideoSummary(BaseModel):
    """Latest video for a creation, surfaced inline in the creations list."""

    status: str  # pending|processing|completed|failed
    cover_url: str | None = None
    video_url: str | None = None


class CreationResponse(CreationBase):
    id: uuid.UUID
    merchant_id: uuid.UUID
    offer_id: uuid.UUID | None = None
    source_app: str
    created_at: datetime
    updated_at: datetime
    # Video summary — count + latest video. Populated by service layer.
    video_count: int = 0
    latest_video: CreationVideoSummary | None = None
    # Structured script from Script Writer (null for plain/manual creations)
    structured_content: dict[str, Any] | None = None

    model_config = {"from_attributes": True}
