from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class UserPromptPreset(Base):
    """User-level override for system default prompts.
    
    Only stores user customizations; system defaults remain in code/files.
    Deleting a row restores the system default for that preset_key.
    """
    __tablename__ = "user_prompt_presets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Stable key: script.base.zh, topic.system.zh, kb.infer.en, etc.
    preset_key = Column(String(128), nullable=False, index=True)
    
    # Display metadata (denormalized for fast list queries)
    title = Column(String(256), nullable=False)
    category = Column(String(64), nullable=False)  # script_writer | topic_studio | image | knowledge | brandkit | kb_qa
    lang = Column(String(16), nullable=False)  # zh | en | multi
    
    # User's override content
    content = Column(Text, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    from sqlalchemy import UniqueConstraint
    
    __table_args__ = (
        UniqueConstraint('user_id', 'preset_key', name='uq_user_prompt_preset_user_key'),
        {"schema": None},
    )

    def __repr__(self):
        return f"<UserPromptPreset {self.preset_key} user={self.user_id}>"