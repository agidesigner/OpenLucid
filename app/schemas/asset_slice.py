import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AssetSliceResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    slice_type: str
    start_ms: int | None = None
    end_ms: int | None = None
    transcript: str | None = None
    summary: str | None = None
    usage_tags_json: dict[str, Any] | None = None
    scene_tags_json: dict[str, Any] | None = None
    audience_tags_json: dict[str, Any] | None = None
    selling_point_refs_json: dict[str, Any] | None = None
    channel_fit_json: dict[str, Any] | None = None
    hook_score: float | None = None
    proof_score: float | None = None
    reuse_score: float | None = None
    confidence: float | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
