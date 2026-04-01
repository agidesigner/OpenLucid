import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class BrandKit(BaseModel):
    __tablename__ = "brandkits"

    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    style_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    product_visual_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    service_scene_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    persona_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    visual_do_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    visual_dont_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reference_prompt_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
