import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TopicPlanGenerateRequest(BaseModel):
    offer_id: uuid.UUID
    channel: str | None = None
    language: str = "zh-CN"
    count: int = Field(5, ge=1, le=20)
    strategy_unit_id: uuid.UUID | None = None
    config_id: str | None = None


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
    strategy_unit_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TopicPlanGenerateResponse(BaseModel):
    offer_id: uuid.UUID
    count: int
    plans: list[TopicPlanResponse]
    thinking: str | None = None
