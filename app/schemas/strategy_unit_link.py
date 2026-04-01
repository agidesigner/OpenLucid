import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import AssetLinkRole, KnowledgeLinkRole
from app.schemas.knowledge import KnowledgeItemResponse
from app.schemas.asset import AssetResponse


class KnowledgeLinkCreate(BaseModel):
    knowledge_item_id: uuid.UUID
    role: KnowledgeLinkRole = KnowledgeLinkRole.GENERAL
    priority: int = 0
    note: str | None = None


class KnowledgeLinkResponse(BaseModel):
    id: uuid.UUID
    strategy_unit_id: uuid.UUID
    knowledge_item_id: uuid.UUID
    role: str
    priority: int
    note: str | None = None
    created_at: datetime
    updated_at: datetime
    knowledge_item: KnowledgeItemResponse | None = None

    model_config = {"from_attributes": True}


class AssetLinkCreate(BaseModel):
    asset_id: uuid.UUID
    role: AssetLinkRole = AssetLinkRole.GENERAL
    priority: int = 0
    note: str | None = None


class AssetLinkResponse(BaseModel):
    id: uuid.UUID
    strategy_unit_id: uuid.UUID
    asset_id: uuid.UUID
    role: str
    priority: int
    note: str | None = None
    created_at: datetime
    updated_at: datetime
    asset: AssetResponse | None = None

    model_config = {"from_attributes": True}
