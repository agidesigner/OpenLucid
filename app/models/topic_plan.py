import uuid

from sqlalchemy import Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class TopicPlan(BaseModel):
    __tablename__ = "topic_plans"

    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    offer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="kb")
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_audience_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    target_scenario_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    recommended_asset_ids_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    score_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_conversion: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_asset_readiness: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    user_rating: Mapped[int | None] = mapped_column(nullable=True)  # 1=like, -1=dislike, NULL=unrated
    strategy_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    # Trend-bridge persistence — populated only when the plan was
    # generated from external_context_text (source_mode=trend_bridge).
    # Without these, the script-writer step has no way to know what
    # trend the topic was riding, and copy reverts to generic KB output.
    hotspot_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    do_not_associate_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    relevance_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    risk_of_forced_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Pydantic-friendly aliases — TopicPlanResponse uses ``hotspot`` and
    # ``do_not_associate`` (without the ``_json`` suffix the column name
    # carries). ``from_attributes=True`` reads via getattr, so a property
    # plus a same-named instance attribute (set after repo.create) both
    # resolve. Property is the read-side default; service code may also
    # set ``self.do_not_associate = ...`` directly to override.
    @property
    def hotspot(self) -> dict | None:
        return self.hotspot_json

    @property
    def do_not_associate(self) -> list | None:
        return self.do_not_associate_json
