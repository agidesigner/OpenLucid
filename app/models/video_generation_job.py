import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class VideoGenerationJob(BaseModel):
    """An asynchronous video generation task tied to a Creation.

    Lifecycle:
        pending → processing → (completed | failed)

    State is updated lazily on GET (no background poller). The provider's
    task_id is stored in `provider_task_id` so we can refresh status by calling
    the original provider config.

    `provider_config_id` is nullable + ON DELETE SET NULL: if the user deletes
    the provider config after a video has been generated, we keep the row
    (with its video_url) but lose the ability to refresh status.
    """

    __tablename__ = "video_generation_jobs"

    creation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("creations.id", ondelete="CASCADE"),
        nullable=False,
    )
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

    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
