import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TopicPlanGenerateRequest(BaseModel):
    offer_id: uuid.UUID
    channel: str | None = None
    # Explicit output language — ``None`` means "follow KB detection".
    # Any string ('zh-CN' / 'en') overrides detection.
    language: str | None = None
    count: int = Field(5, ge=1, le=20)
    strategy_unit_id: uuid.UUID | None = None
    config_id: str | None = None
    # Override the saved config's default model with any model the
    # config's endpoint actually exposes (resolved in the WebUI by
    # fetching /llm/{config_id}/models). Plumbed through to
    # ``get_ai_adapter`` after endpoint resolution.
    model_override: str | None = None
    instruction: str | None = Field(None, max_length=1000)
    # External hot-topic / trend context (per-call, not persisted).
    # When provided, the topic_studio prompt switches into "trend bridge"
    # mode: it must find a real KB anchor that connects to this trend
    # before generating any topic. See plan in
    # /Users/ajin/.claude/plans/index-html-offer-kb-offer-kb-velvety-pnueli.md
    external_context_text: str | None = Field(None, max_length=8000)
    external_context_url: str | None = None


class HotspotSummary(BaseModel):
    """Structured extraction of an external trend, produced by the LLM
    on the first step of trend-bridge mode. Used as a quality diagnostic
    so the user can sanity-check what the model thought the hot topic
    actually was before it generated topics from it."""
    event: str | None = None
    keywords: list[str] | None = None
    public_attention: str | None = None
    risk_zones: list[str] | None = None


class TopicPlanResponse(BaseModel):
    id: uuid.UUID
    merchant_id: uuid.UUID
    offer_id: uuid.UUID
    source_mode: str
    title: str
    angle: str | None = None
    target_audience_json: Any = None
    target_scenario_json: Any = None
    hook: str | None = None
    key_points_json: Any = None
    recommended_asset_ids_json: Any = None
    channel: str | None = None
    language: str
    score_relevance: float | None = None
    score_conversion: float | None = None
    score_asset_readiness: float | None = None
    status: str
    user_rating: int | None = None
    strategy_unit_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    # Trend-bridge augmentations — now persisted on the row alongside
    # the plan, so script-writer / content-studio can fetch them via
    # topic_plan_id and keep the trend context alive downstream.
    relevance_tier: Literal["strong", "medium", "weak"] | None = None
    risk_of_forced_relevance: float | None = None
    do_not_associate: list[str] | None = None
    # The structured read of the external trend (event, keywords,
    # public_attention, risk_zones) — same payload across all plans
    # in one generation batch. Populated from ``TopicPlan.hotspot_json``
    # via the ``hotspot`` property on the model.
    hotspot: HotspotSummary | None = None

    model_config = {"from_attributes": True}


class TopicPlanGenerateResponse(BaseModel):
    offer_id: uuid.UUID
    count: int
    plans: list[TopicPlanResponse]
    thinking: str | None = None
    # Set only when external_context was provided. The LLM's structured
    # read of the trend — surfaced to the UI so the user can verify the
    # model understood the input before reading the topic cards.
    hotspot: HotspotSummary | None = None
