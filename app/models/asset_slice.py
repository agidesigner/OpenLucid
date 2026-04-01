import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.asset import Asset


class AssetSlice(BaseModel):
    __tablename__ = "asset_slices"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slice_type: Mapped[str] = mapped_column(String(32), nullable=False)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_tags_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scene_tags_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    audience_tags_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    selling_point_refs_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    channel_fit_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hook_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    proof_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reuse_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    asset: Mapped["Asset"] = relationship("Asset", back_populates="slices")
