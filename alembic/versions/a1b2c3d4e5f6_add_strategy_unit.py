"""add_strategy_unit

Revision ID: a1b2c3d4e5f6
Revises: d7421f314b0f
Create Date: 2026-03-26 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d7421f314b0f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create strategy_units table
    op.create_table(
        "strategy_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("audience_segment", sa.String(255), nullable=True),
        sa.Column("scenario", sa.String(255), nullable=True),
        sa.Column("marketing_objective", sa.String(32), nullable=True),
        sa.Column("channel", sa.String(64), nullable=True),
        sa.Column("strategy_stage", sa.String(32), nullable=False, server_default="exploring"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("language", sa.String(16), nullable=False, server_default="zh-CN"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("asset_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("topic_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_score", sa.Float(), nullable=True),
        sa.Column("trend_status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_units_merchant_id", "strategy_units", ["merchant_id"])
    op.create_index("ix_strategy_units_offer_id", "strategy_units", ["offer_id"])

    # 2. Add strategy_unit_id to knowledge_items
    op.add_column("knowledge_items", sa.Column("strategy_unit_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_knowledge_items_strategy_unit_id", "knowledge_items", ["strategy_unit_id"])

    # 3. Add strategy_unit_id to assets
    op.add_column("assets", sa.Column("strategy_unit_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_assets_strategy_unit_id", "assets", ["strategy_unit_id"])

    # 4. Add strategy_unit_id to topic_plans
    op.add_column("topic_plans", sa.Column("strategy_unit_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_topic_plans_strategy_unit_id", "topic_plans", ["strategy_unit_id"])

    # 5. Add primary_objective to offers
    op.add_column("offers", sa.Column("primary_objective", sa.String(32), nullable=True))

    # 6. Add secondary_objectives_json to offers
    op.add_column("offers", sa.Column("secondary_objectives_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "secondary_objectives_json")
    op.drop_column("offers", "primary_objective")

    op.drop_index("ix_topic_plans_strategy_unit_id", table_name="topic_plans")
    op.drop_column("topic_plans", "strategy_unit_id")

    op.drop_index("ix_assets_strategy_unit_id", table_name="assets")
    op.drop_column("assets", "strategy_unit_id")

    op.drop_index("ix_knowledge_items_strategy_unit_id", table_name="knowledge_items")
    op.drop_column("knowledge_items", "strategy_unit_id")

    op.drop_index("ix_strategy_units_offer_id", table_name="strategy_units")
    op.drop_index("ix_strategy_units_merchant_id", table_name="strategy_units")
    op.drop_table("strategy_units")
