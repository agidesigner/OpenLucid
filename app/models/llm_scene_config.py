from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LLMSceneConfig(Base):
    __tablename__ = "llm_scene_configs"

    scene: Mapped[str] = mapped_column(String(50), primary_key=True)
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL"), nullable=True
    )
