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
    # Explicit output language — ``None`` (omit) means "follow KB's detected
    # language". Any string ('zh-CN' / 'en') overrides detection. Callers
    # that don't expose a language picker should omit this field.
    language: str | None = None
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


# ── Script Writer ─────────────────────────────────────────────


class ScriptWriterRequest(BaseModel):
    offer_id: uuid.UUID
    strategy_unit_id: uuid.UUID | None = None
    system_prompt: str = Field("", max_length=20000)  # kept for backwards compat; ignored when composer fields set
    topic: str = Field("", max_length=2000)
    goal: str = Field("seeding", pattern="^(reach_growth|lead_generation|conversion|education|traffic_redirect|other|seeding|knowledge_sharing|brand_awareness)$")
    tone: str | None = None
    word_count: int = Field(150, ge=50, le=2000)
    cta: str | None = None
    industry: str | None = None
    reference: str | None = Field(None, max_length=5000)
    extra_req: str | None = Field(None, max_length=2000)
    # Explicit output language — ``None`` means "follow KB detection".
    # Any string ('zh-CN' / 'en') overrides detection.
    language: str | None = None
    config_id: str | None = None
    # ── Composer dimensions (new) ─────────────────────────────────
    platform_id: str | None = None    # e.g. "douyin" — defaults to "douyin" if not set
    persona_id: str | None = None     # e.g. "tech_founder"
    goal_id: str | None = None        # e.g. "seeding" (replaces old `goal` enum eventually)
    structure_id: str | None = None   # e.g. "hook_body_cta"
    save_creation: bool = True        # whether to persist the result as a Creation
    # ── Invocation-context fields (Wave 5) ────────────────────────
    source_app: str = "script_writer"         # attribution when the service auto-saves; MCP callers set "mcp:external"
    topic_plan_id: uuid.UUID | None = None    # if set and `topic` is empty, the plan's title/hook/angle/key_points drive the prompt
