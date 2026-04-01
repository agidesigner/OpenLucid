"""structured tags + hook/reuse scores on assets

Revision ID: j7e8f9a0b1c2
Revises: i6d7e8f9a0b1
Create Date: 2026-03-29 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j7e8f9a0b1c2"
down_revision: Union[str, None] = "i6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add score fields to assets (IF NOT EXISTS — idempotent)
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS hook_score FLOAT")
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS reuse_score FLOAT")

    # Migrate old flat array tags to {"legacy": [...]} dict structure
    op.execute("""
        UPDATE assets
        SET tags_json = jsonb_build_object('legacy', tags_json)
        WHERE tags_json IS NOT NULL
          AND jsonb_typeof(tags_json) = 'array'
    """)


def downgrade() -> None:
    # Restore flat array from legacy key
    op.execute("""
        UPDATE assets
        SET tags_json = tags_json -> 'legacy'
        WHERE tags_json IS NOT NULL
          AND jsonb_typeof(tags_json) = 'object'
          AND tags_json ? 'legacy'
    """)

    op.drop_column("assets", "reuse_score")
    op.drop_column("assets", "hook_score")
