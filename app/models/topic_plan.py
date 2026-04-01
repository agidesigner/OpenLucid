import uuid

from sqlalchemy import Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class TopicPlan(BaseModel):
    __tablename__ = "topic_plans"

    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    offer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="kb")
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_audience_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    target_scenario_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recommended_asset_ids_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    score_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_conversion: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_asset_readiness: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    strategy_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
