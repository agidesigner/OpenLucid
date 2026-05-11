"""LLM call trace records for the developer tools tracing UI."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class LLMTrace(BaseModel):
    __tablename__ = "llm_traces"

    # Call identity
    scene_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    call_type: Mapped[str] = mapped_column(String(32), nullable=False)  # chat / chat_json / chat_stream / chat_vision
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")

    # Input
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Output
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    thinking_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)

    # Metrics
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Associations
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Timing
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_llm_traces_scene_created", "scene_key", "created_at"),
        Index("ix_llm_traces_status_created", "status", "created_at"),
    )