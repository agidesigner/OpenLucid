import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import BrandKitAssetRole, BrandKitStatus, ScopeType
from app.schemas.asset import AssetResponse


class BrandKitCreate(BaseModel):
    scope_type: ScopeType
    scope_id: uuid.UUID
    # name / description are optional — if omitted, the service derives them
    # from the scope parent (merchant.name / offer.name). Retained in the
    # schema only for back-compat with external callers (e.g. older MCP).
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    style_profile_json: Any = None
    product_visual_profile_json: Any = None
    service_scene_profile_json: Any = None
    persona_profile_json: Any = None
    visual_do_json: Any = None
    visual_dont_json: Any = None
    reference_prompt_json: Any = None
    status: BrandKitStatus = BrandKitStatus.ACTIVE


class BrandKitUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    style_profile_json: Any = None
    product_visual_profile_json: Any = None
    service_scene_profile_json: Any = None
    persona_profile_json: Any = None
    visual_do_json: Any = None
    visual_dont_json: Any = None
    reference_prompt_json: Any = None
    # status field kept for backward compat but not exposed in UI


class BrandKitResponse(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    # Nullable — frontend derives display from the scope parent when missing.
    name: str | None = None
    description: str | None = None
    style_profile_json: Any = None
    product_visual_profile_json: Any = None
    service_scene_profile_json: Any = None
    persona_profile_json: Any = None
    visual_do_json: Any = None
    visual_dont_json: Any = None
    reference_prompt_json: Any = None
    inherited_fields: list[str] | None = None
    overridden_fields: list[str] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BrandKitAssetLinkCreate(BaseModel):
    asset_id: uuid.UUID
    role: BrandKitAssetRole = BrandKitAssetRole.REFERENCE_IMAGE
    priority: int = 0
    note: str | None = None


class BrandKitAssetLinkResponse(BaseModel):
    id: uuid.UUID
    brandkit_id: uuid.UUID
    asset_id: uuid.UUID
    role: str
    priority: int
    note: str | None = None
    created_at: datetime
    updated_at: datetime
    asset: AssetResponse | None = None

    model_config = {"from_attributes": True}
