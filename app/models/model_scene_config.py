from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ModelSceneConfig(Base):
    __tablename__ = "model_scene_configs"

    scene_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    model_type: Mapped[str] = mapped_column(String(50), primary_key=True)  # "text_llm", "video_gen", etc.
    config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL"), nullable=True
    )
    # Per-scene override of which model to invoke on the bound endpoint. When
    # NULL, callers fall back to llm_configs.model_name (the endpoint's
    # default). Lets one llm_config row serve many models (aggregator proxies
    # like OneAPI / LiteLLM / enterprise gateways).
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
