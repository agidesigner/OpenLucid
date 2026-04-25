import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import KnowledgeSourceType, KnowledgeType, ScopeType


class KnowledgeItemCreate(BaseModel):
    scope_type: ScopeType
    scope_id: uuid.UUID
    knowledge_type: KnowledgeType = KnowledgeType.GENERAL
    title: str = Field(..., min_length=1, max_length=512)
    content_raw: str | None = None
    content_structured_json: dict[str, Any] | None = None
    source_type: KnowledgeSourceType = KnowledgeSourceType.MANUAL
    source_ref: str | None = None
    language: str = "zh-CN"
    tags_json: dict[str, Any] | None = None
    # Symmetric to KnowledgeItemUpdate.confidence: lets a single REST
    # POST or CLI ``add-knowledge --confidence 0.95`` write the
    # AI-inference score in one round-trip. Pre-v1.1.4 the field was
    # absent here so any --confidence flag from the CLI silently
    # round-tripped to NULL — caught during the v1.1.4 dogfood demo.
    confidence: float | None = None


class KnowledgeItemUpdate(BaseModel):
    knowledge_type: KnowledgeType | None = None
    title: str | None = Field(None, min_length=1, max_length=512)
    content_raw: str | None = None
    content_structured_json: dict[str, Any] | None = None
    language: str | None = None
    tags_json: dict[str, Any] | None = None
    # Source/confidence fields. Pre-v1.1.3 these were write-once at
    # create time, so a re-inference run that wanted to bump
    # ``confidence`` from 0.85 → 0.95 silently no-op'd through the PATCH
    # endpoint (drift between create-side and update-side schemas).
    source_type: KnowledgeSourceType | None = None
    source_ref: str | None = None
    confidence: float | None = None


class KnowledgeItemResponse(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    knowledge_type: str
    title: str
    content_raw: str | None = None
    content_structured_json: dict[str, Any] | None = None
    source_type: str
    source_ref: str | None = None
    language: str
    confidence: float | None = None
    tags_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeBatchImport(BaseModel):
    scope_type: ScopeType
    scope_id: uuid.UUID
    items: list[KnowledgeItemCreate]


class KnowledgeBatchResult(BaseModel):
    created: int
    items: list[KnowledgeItemResponse]


class KnowledgeBatchUpsertResult(BaseModel):
    updated: int
    created: int
    items: list[KnowledgeItemResponse]
