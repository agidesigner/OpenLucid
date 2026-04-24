import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import (
    BrandKitAssetRole,
    BrandKitColorRole,
    BrandKitFontRole,
    BrandKitStatus,
    ScopeType,
)
from app.schemas.asset import AssetResponse


# ── BrandKit ─────────────────────────────────────────────────


class BrandKitCreate(BaseModel):
    scope_type: ScopeType
    scope_id: uuid.UUID
    # name / description are optional — if omitted, the service derives them
    # from the scope parent (merchant.name / offer.name). Retained in the
    # schema only for back-compat with external callers (e.g. older MCP).
    name: str | None = Field(None, max_length=255)
    description: str | None = None
    brand_voice: str | None = None
    status: BrandKitStatus = BrandKitStatus.ACTIVE


class BrandKitUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    brand_voice: str | None = None


class BrandKitResponse(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    # Nullable — frontend derives display from the scope parent when missing.
    name: str | None = None
    description: str | None = None
    brand_voice: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Color ────────────────────────────────────────────────────


class BrandKitColorCreate(BaseModel):
    role: BrandKitColorRole = BrandKitColorRole.CUSTOM
    hex: str = Field(..., min_length=4, max_length=9)  # #RGB, #RRGGBB, or #RRGGBBAA
    priority: int = 0


class BrandKitColorUpdate(BaseModel):
    role: BrandKitColorRole | None = None
    hex: str | None = Field(None, min_length=4, max_length=9)
    priority: int | None = None


class BrandKitColorResponse(BaseModel):
    id: uuid.UUID
    brandkit_id: uuid.UUID
    role: str
    hex: str
    priority: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Font ─────────────────────────────────────────────────────


class BrandKitFontCreate(BaseModel):
    role: BrandKitFontRole = BrandKitFontRole.CUSTOM
    font_name: str = Field(..., max_length=255)
    font_url: str | None = None
    priority: int = 0


class BrandKitFontUpdate(BaseModel):
    role: BrandKitFontRole | None = None
    font_name: str | None = Field(None, max_length=255)
    font_url: str | None = None
    priority: int | None = None


class BrandKitFontResponse(BaseModel):
    id: uuid.UUID
    brandkit_id: uuid.UUID
    role: str
    font_name: str
    font_url: str | None = None
    priority: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Asset link ───────────────────────────────────────────────


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
