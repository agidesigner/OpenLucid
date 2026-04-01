"""add strategy unit link tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-26 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create strategy_unit_knowledge_links table
    op.create_table(
        "strategy_unit_knowledge_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["strategy_unit_id"], ["strategy_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_item_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_unit_id", "knowledge_item_id", name="uq_su_knowledge_link"),
    )
    op.create_index("ix_su_knowledge_links_strategy_unit_id", "strategy_unit_knowledge_links", ["strategy_unit_id"])
    op.create_index("ix_su_knowledge_links_knowledge_item_id", "strategy_unit_knowledge_links", ["knowledge_item_id"])

    # 2. Create strategy_unit_asset_links table
    op.create_table(
        "strategy_unit_asset_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["strategy_unit_id"], ["strategy_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("strategy_unit_id", "asset_id", name="uq_su_asset_link"),
    )
    op.create_index("ix_su_asset_links_strategy_unit_id", "strategy_unit_asset_links", ["strategy_unit_id"])
    op.create_index("ix_su_asset_links_asset_id", "strategy_unit_asset_links", ["asset_id"])

    # 3. Drop strategy_unit_id from knowledge_items
    op.drop_index("ix_knowledge_items_strategy_unit_id", table_name="knowledge_items")
    op.drop_column("knowledge_items", "strategy_unit_id")

    # 4. Drop strategy_unit_id from assets
    op.drop_index("ix_assets_strategy_unit_id", table_name="assets")
    op.drop_column("assets", "strategy_unit_id")


def downgrade() -> None:
    # Restore strategy_unit_id on assets
    op.add_column("assets", sa.Column("strategy_unit_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_assets_strategy_unit_id", "assets", ["strategy_unit_id"])

    # Restore strategy_unit_id on knowledge_items
    op.add_column("knowledge_items", sa.Column("strategy_unit_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_knowledge_items_strategy_unit_id", "knowledge_items", ["strategy_unit_id"])

    # Drop link tables
    op.drop_index("ix_su_asset_links_asset_id", table_name="strategy_unit_asset_links")
    op.drop_index("ix_su_asset_links_strategy_unit_id", table_name="strategy_unit_asset_links")
    op.drop_table("strategy_unit_asset_links")

    op.drop_index("ix_su_knowledge_links_knowledge_item_id", table_name="strategy_unit_knowledge_links")
    op.drop_index("ix_su_knowledge_links_strategy_unit_id", table_name="strategy_unit_knowledge_links")
    op.drop_table("strategy_unit_knowledge_links")
