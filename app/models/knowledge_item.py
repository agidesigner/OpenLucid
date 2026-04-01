import uuid

from sqlalchemy import Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class KnowledgeItem(BaseModel):
    __tablename__ = "knowledge_items"

    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    knowledge_type: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_structured_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
