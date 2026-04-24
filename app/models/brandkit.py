import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class BrandKit(BaseModel):
    __tablename__ = "brandkits"

    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # name / description are derived from the scope parent (merchant.name / offer.name)
    # on the frontend. Columns kept (nullable) for API backward compatibility; new rows
    # are created with NULL and back-populated from the parent on read if needed.
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Single structured text: the brand's voice / tone / DO+DONT words. This is the
    # one field that text generation (composer's BRAND layer) actually reads. The
    # seven previous JSONB fields were never consumed anywhere and were migrated
    # into a concatenated seed inside this column in migration b5q6r7s8t9u0.
    brand_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class BrandKitColor(BaseModel):
    __tablename__ = "brandkit_colors"

    brandkit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brandkits.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # primary / secondary / tertiary / accent / custom
    hex: Mapped[str] = mapped_column(String(9), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BrandKitFont(BaseModel):
    __tablename__ = "brandkit_fonts"

    brandkit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brandkits.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # title / body / custom
    font_name: Mapped[str] = mapped_column(String(255), nullable=False)
    font_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
