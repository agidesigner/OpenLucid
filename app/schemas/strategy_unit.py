import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import MarketingObjective, TrendStatus


class StrategyUnitCreate(BaseModel):
    merchant_id: uuid.UUID
    offer_id: uuid.UUID
    # Optional at the API layer: when omitted (or blank), the service
    # auto-summarizes a name from audience_segment + scenario +
    # marketing_objective via LLM. Callers that want an explicit name
    # still win.
    name: str | None = Field(None, max_length=255)
    audience_segment: str | None = None
    scenario: str | None = None
    marketing_objective: MarketingObjective | None = None
    channel: str | None = None
    status: str = "active"
    language: str = "zh-CN"
    notes: str | None = None


class StrategyUnitUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    audience_segment: str | None = None
    scenario: str | None = None
    marketing_objective: MarketingObjective | None = None
    channel: str | None = None
    status: str | None = None
    language: str | None = None
    notes: str | None = None
    asset_count: int | None = None
    topic_count: int | None = None
    coverage_score: float | None = None
    trend_status: TrendStatus | None = None


class StrategyUnitResponse(BaseModel):
    id: uuid.UUID
    merchant_id: uuid.UUID
    offer_id: uuid.UUID
    name: str
    audience_segment: str | None = None
    scenario: str | None = None
    marketing_objective: str | None = None
    channel: str | None = None
    status: str
    language: str
    notes: str | None = None
    asset_count: int
    topic_count: int
    coverage_score: float | None = None
    trend_status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
