import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import MarketingObjective, OfferModel, OfferType


class OfferCreate(BaseModel):
    merchant_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    offer_type: OfferType = OfferType.PRODUCT
    offer_model: OfferModel | None = None
    description: str | None = None
    positioning: str | None = None
    target_audience_json: dict[str, Any] | None = None
    target_scenarios_json: dict[str, Any] | None = None
    core_selling_points_json: dict[str, Any] | None = None
    objections_json: dict[str, Any] | None = None
    proofs_json: dict[str, Any] | None = None
    pricing_info_json: dict[str, Any] | None = None
    locale: str = "zh-CN"
    primary_objective: MarketingObjective | None = None
    secondary_objectives_json: dict[str, Any] | None = None


class OfferUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    offer_type: OfferType | None = None
    offer_model: OfferModel | None = None
    description: str | None = None
    positioning: str | None = None
    target_audience_json: dict[str, Any] | None = None
    target_scenarios_json: dict[str, Any] | None = None
    core_selling_points_json: dict[str, Any] | None = None
    objections_json: dict[str, Any] | None = None
    proofs_json: dict[str, Any] | None = None
    pricing_info_json: dict[str, Any] | None = None
    locale: str | None = None
    status: str | None = None
    primary_objective: MarketingObjective | None = None
    secondary_objectives_json: dict[str, Any] | None = None


class OfferResponse(BaseModel):
    id: uuid.UUID
    merchant_id: uuid.UUID
    name: str
    offer_type: str
    offer_model: str | None = None
    description: str | None = None
    positioning: str | None = None
    target_audience_json: dict[str, Any] | None = None
    target_scenarios_json: dict[str, Any] | None = None
    core_selling_points_json: dict[str, Any] | None = None
    objections_json: dict[str, Any] | None = None
    proofs_json: dict[str, Any] | None = None
    pricing_info_json: dict[str, Any] | None = None
    locale: str
    status: str
    primary_objective: str | None = None
    secondary_objectives_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
