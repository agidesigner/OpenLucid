from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MediaCapabilityDefault(Base):
    """Default provider + model for non-LLM media capabilities.

    capability: 'image_gen' | 'video_gen' | 'tts'
    For image_gen / video_gen: provider_config_id + model_code
    For tts: provider_config_id + voice_id
    """
    __tablename__ = "media_capability_defaults"

    capability: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_provider_configs.id", ondelete="CASCADE"), nullable=True
    )
    model_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    voice_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
