import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.merchant import Merchant


class Offer(BaseModel):
    __tablename__ = "offers"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    offer_type: Mapped[str] = mapped_column(String(32), nullable=False, default="product")
    offer_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    positioning: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_audience_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    target_scenarios_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    core_selling_points_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    objections_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    proofs_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pricing_info_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    primary_objective: Mapped[str | None] = mapped_column(String(32), nullable=True)
    secondary_objectives_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    merchant: Mapped["Merchant"] = relationship("Merchant", back_populates="offers")
