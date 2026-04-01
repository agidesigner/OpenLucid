from sqlalchemy import Column, String

from app.models.base import BaseModel


class McpToken(BaseModel):
    __tablename__ = "mcp_tokens"

    label = Column(String(255), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
