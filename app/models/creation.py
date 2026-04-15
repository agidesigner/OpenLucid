import uuid

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Creation(BaseModel):
    """A finished content piece saved back to OpenLucid.

    Source can be:
      - external AI client via MCP `save_creation` tool (source_app="mcp:claude-code", etc.)
      - internal app like Topic Studio (source_app="topic_studio")

    `content` is plain text — no schema lock-in. Creations are the user's
    final output, not structured data.
    """

    __tablename__ = "creations"

    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    offer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_app: Mapped[str] = mapped_column(String(80), nullable=False, default="manual")
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Stores the structured script output from Script Writer. Shape:
    # {
    #   "platform_id": "douyin", "structure_id": "hook_body_cta",
    #   "persona_id": "tech_founder", "goal_id": "seeding",
    #   "content_type": "video",
    #   "sections": {
    #     "hook": {"text": "...", "visual_direction": "...", "duration_seconds": 5},
    #     "body": {"text": "...", "visual_direction": "...", "duration_seconds": 55},
    #     "cta":  {"text": "...", "visual_direction": "...", "duration_seconds": 5}
    #   }
    # }
