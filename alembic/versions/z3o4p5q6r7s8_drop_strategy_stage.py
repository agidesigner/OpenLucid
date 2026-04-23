"""Drop strategy_units.strategy_stage

Revision ID: z3o4p5q6r7s8
Revises: y2n3o4p5q6r7
Create Date: 2026-04-23

The "策略阶段" concept (exploring / rising / stable / declining) is removed
from the product. The field was write-only — no backend service, MCP tool,
or prompt consumed it — so dropping the column is safe.
"""
from alembic import op
import sqlalchemy as sa

revision = "z3o4p5q6r7s8"
down_revision = "y2n3o4p5q6r7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_units DROP COLUMN IF EXISTS strategy_stage;")


def downgrade() -> None:
    op.add_column(
        "strategy_units",
        sa.Column(
            "strategy_stage",
            sa.String(length=32),
            nullable=False,
            server_default="exploring",
        ),
    )
