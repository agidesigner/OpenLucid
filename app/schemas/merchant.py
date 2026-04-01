import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import MerchantType


class MerchantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    merchant_type: MerchantType = MerchantType.GOODS
    default_locale: str = "zh-CN"
    supported_locales: list[str] | None = None
    brand_profile_json: dict[str, Any] | None = None
    tone_profile_json: dict[str, Any] | None = None
    compliance_profile_json: dict[str, Any] | None = None


class MerchantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    merchant_type: MerchantType | None = None
    default_locale: str | None = None
    supported_locales: list[str] | None = None
    brand_profile_json: dict[str, Any] | None = None
    tone_profile_json: dict[str, Any] | None = None
    compliance_profile_json: dict[str, Any] | None = None


class MerchantResponse(BaseModel):
    id: uuid.UUID
    name: str
    merchant_type: str
    default_locale: str
    supported_locales: list[str] | None = None
    brand_profile_json: dict[str, Any] | None = None
    tone_profile_json: dict[str, Any] | None = None
    compliance_profile_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
