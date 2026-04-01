"""add file_hash to assets for deduplication

Revision ID: k8f9a0b1c2d3
Revises: j7e8f9a0b1c2
Create Date: 2026-03-29 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k8f9a0b1c2d3"
down_revision: Union[str, None] = "j7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_assets_file_hash ON assets (file_hash)")


def downgrade() -> None:
    op.drop_index("ix_assets_file_hash", table_name="assets")
    op.drop_column("assets", "file_hash")
