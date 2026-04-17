from datetime import datetime

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class McpToken(BaseModel):
    __tablename__ = "mcp_tokens"

    label = Column(String(255), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
