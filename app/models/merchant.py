from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.offer import Offer


class Merchant(BaseModel):
    __tablename__ = "merchants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_type: Mapped[str] = mapped_column(String(32), nullable=False, default="goods")
    default_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    supported_locales: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    brand_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tone_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    compliance_profile_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    offers: Mapped[list["Offer"]] = relationship("Offer", back_populates="merchant", lazy="selectin")
