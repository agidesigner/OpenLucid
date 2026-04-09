import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreationBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=1)
    content_type: str = Field("general", max_length=50)
    tags: list[str] | None = None
    source_note: str | None = None


class CreationCreate(CreationBase):
    merchant_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None
    source_app: str = Field("manual", max_length=80)


class CreationUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=512)
    content: str | None = Field(None, min_length=1)
    content_type: str | None = Field(None, max_length=50)
    tags: list[str] | None = None
    source_note: str | None = None


class CreationResponse(CreationBase):
    id: uuid.UUID
    merchant_id: uuid.UUID
    offer_id: uuid.UUID | None = None
    source_app: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
