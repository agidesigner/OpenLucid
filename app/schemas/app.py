import uuid
from typing import Any

from pydantic import BaseModel, Field


class AppDefinitionResponse(BaseModel):
    app_id: str
    name: str
    slug: str
    description: str
    icon: str
    category: str
    task_type: str
    required_entities: list[str]
    entry_modes: list[str]
    status: str
    is_builtin: bool
    version: str


class TopicStudioRunRequest(BaseModel):
    offer_id: uuid.UUID
    strategy_unit_id: uuid.UUID | None = None
    count: int = Field(5, ge=1, le=20)
    language: str = "zh-CN"
    channel: str | None = None
    config_id: str | None = None


class TopicStudioContextPreview(BaseModel):
    offer_id: uuid.UUID
    offer_name: str
    strategy_unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    audience_segment: str | None = None
    scenario: str | None = None
    channel: str | None = None
    marketing_objective: str | None = None
    knowledge_count: int
    linked_knowledge_count: int
    asset_count: int
    linked_asset_count: int
    is_ready: bool


# ── KB QA ──────────────────────────────────────────────────────


class KBQAStyleResponse(BaseModel):
    style_id: str
    name: str
    description: str
    icon: str


class KBQAAskRequest(BaseModel):
    offer_id: uuid.UUID
    question: str = Field(..., min_length=1, max_length=2000)
    style_id: str = "professional"
    language: str = "zh-CN"
    config_id: str | None = None


class KBQAReferencedKnowledge(BaseModel):
    knowledge_id: uuid.UUID | None = None
    title: str
    knowledge_type: str


class KBQAAskResponse(BaseModel):
    answer: str
    style_id: str
    referenced_knowledge: list[KBQAReferencedKnowledge]
    knowledge_count: int
    has_relevant_knowledge: bool
    thinking: str | None = None
