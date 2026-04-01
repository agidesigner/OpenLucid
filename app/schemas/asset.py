import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.domain.enums import AssetType, ScopeType


class AssetUploadMeta(BaseModel):
    scope_type: ScopeType
    scope_id: uuid.UUID
    asset_type: AssetType
    language: str = "zh-CN"


class AssetCopyCreate(BaseModel):
    scope_type: ScopeType
    scope_id: uuid.UUID
    title: str
    content_text: str
    tags: dict[str, list[str]] = {}
    language: str = "zh-CN"


class AssetSearchParams(BaseModel):
    q: str | None = None
    asset_type: str | None = None
    tags: str | None = None  # comma-separated
    status: str | None = None
    scope_type: str | None = None
    scope_id: uuid.UUID | None = None
    page: int = 1
    page_size: int = 20


class HighlightsParams(BaseModel):
    scope_type: str | None = None
    scope_id: uuid.UUID | None = None
    min_hook_score: float = 0.0
    min_proof_score: float = 0.0
    min_reuse_score: float = 0.0
    slice_type: str | None = None
    page: int = 1
    page_size: int = 20


class TagAnalyticsItem(BaseModel):
    tag: str
    count: int
    category: str | None = None


class TagAnalyticsResponse(BaseModel):
    items: list[TagAnalyticsItem]


class AssetProcessingJobResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    job_type: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_msg: str | None = None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssetUpdate(BaseModel):
    title: str | None = None
    tags_json: dict[str, list[str]] | None = None


class AssetResponse(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    asset_type: str
    file_name: str
    title: str | None = None
    mime_type: str | None = None
    storage_uri: str | None = None
    preview_uri: str | None = None
    metadata_json: dict[str, Any] | None = None
    parse_status: str
    status: str
    language: str
    tags_json: dict[str, list[str]] | list[str] | None = None
    content_text: str | None = None
    confidence: float | None = None
    hook_score: float | None = None
    reuse_score: float | None = None
    file_hash: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DuplicateCheckResponse(BaseModel):
    exists: bool
    asset: "AssetResponse | None" = None
