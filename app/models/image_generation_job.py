import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class ImageGenerationJob(BaseModel):
    """An asynchronous image generation task.

    Two modes share this table:

      * ``mode='poster'``: standalone marketing poster, driven by an offer's
        selling_point + a hardcoded template. ``offer_id`` + ``template_id``
        + ``brandkit_id`` populated; ``creation_id`` NULL.
      * ``mode='article_cover'``: cover image for a Creation (article).
        ``creation_id`` populated; offer/template/brandkit NULL.

    Lifecycle: pending → processing → (completed | failed)

    GPT-image-1 is synchronous (image bytes in HTTP response), so we run
    the provider call inline and never need ``provider_task_id`` for it.
    The column is kept for future async providers (FLUX/Imagen/Replicate).
    """

    __tablename__ = "image_generation_jobs"

    mode: Mapped[str] = mapped_column(String(32), nullable=False)

    creation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creations.id", ondelete="CASCADE"),
        nullable=True,
    )
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=True,
    )
    brandkit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brandkits.id", ondelete="SET NULL"),
        nullable=True,
    )
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_config_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_provider_configs.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending|processing|completed|failed
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)

    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
