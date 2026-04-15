from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class MediaProviderConfig(BaseModel):
    """Configuration for a third-party media generation provider.

    `provider` is the discriminator: 'chanjing' or 'jogg' (extensible).
    `credentials` is a JSONB whose shape depends on provider:
        chanjing: {"app_id": str, "secret_key": str}
        jogg:     {"api_key": str}
    `defaults` is a JSONB holding the user's preferred avatar/voice/aspect:
        {"avatar_id": str|None, "voice_id": str|None,
         "aspect_ratio": "portrait"|"landscape"|"square"}

    Activation is per-provider (at most one active row per provider type),
    different from LLMConfig (which has at most one active row total) — because
    a user may legitimately want both Chanjing AND Jogg active to cover both
    Chinese and English audiences.
    """

    __tablename__ = "media_provider_configs"

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    credentials: Mapped[dict] = mapped_column(JSONB, nullable=False)
    defaults: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
