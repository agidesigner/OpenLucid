"""User-preference memory captured during refine flows.

A memory_entry is one line of guidance that future generations on the
same scope (offer or merchant) must respect. The capture path is
explicit — UI surfaces a "💾 记住" chip after a successful refine and
the user confirms — so a noisy or one-off correction doesn't pollute
long-term preferences.

The model is deliberately thin:
  * scope_type + scope_id mirror Asset / Knowledge so retrieval can
    reuse the same merge logic ("show me everything that applies to
    THIS offer in THIS merchant").
  * surface narrows which assembler consumes the memory. Cross-cutting
    rules (brand voice constraints) use "all"; image-specific composition
    hints use "image"; script-only tone overrides use "script".
  * content is free text. We did NOT add a kind/category column — the
    distinction between "brand_rule" and "format_rule" had no impact
    on prompt assembly and only added user friction.
"""
import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class MemoryEntry(BaseModel):
    __tablename__ = "memory_entries"

    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 'merchant' (cross-offer brand prefs) | 'offer' (product-specific)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # When scope_type='merchant', this matches merchant_id.
    # When scope_type='offer', this is the offer_id.
    # Kept as a generic UUID so future scope types ('strategy_unit', etc.)
    # don't need a schema change — each new scope just gets a service-
    # layer rule for what scope_id points at.
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # 'all' | 'image' | 'script'
    surface: Mapped[str] = mapped_column(
        String(16), nullable=False, default="all"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 'manual' | 'refine_capture' | 'mcp' — provenance for the audit
    # trail and future "auto-suggest" features.
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual"
    )
    # When source='refine_capture', the parent job_id (image / video).
    # Free text rather than FK because future capture sources may
    # reference IDs from tables that don't exist yet.
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft delete — preserves provenance ("user once asked for this,
    # then turned it off") without a separate audit table. Service
    # layer filters is_active=True by default.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
