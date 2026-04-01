import uuid

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class StrategyUnit(BaseModel):
    __tablename__ = "strategy_units"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    audience_segment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marketing_objective: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="exploring")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
