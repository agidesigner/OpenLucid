import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class StrategyUnitKnowledgeLink(BaseModel):
    __tablename__ = "strategy_unit_knowledge_links"
    __table_args__ = (
        UniqueConstraint("strategy_unit_id", "knowledge_item_id", name="uq_su_knowledge_link"),
    )

    strategy_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategy_units.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    strategy_unit = relationship("StrategyUnit", lazy="selectin")
    knowledge_item = relationship("KnowledgeItem", lazy="selectin")
