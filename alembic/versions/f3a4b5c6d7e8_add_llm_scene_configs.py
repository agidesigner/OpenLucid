"""add llm_scene_configs table

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-03-28 13:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_scene_configs",
        sa.Column("scene", sa.String(50), nullable=False),
        sa.Column("llm_config_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["llm_config_id"],
            ["llm_configs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("scene"),
    )


def downgrade() -> None:
    op.drop_table("llm_scene_configs")
