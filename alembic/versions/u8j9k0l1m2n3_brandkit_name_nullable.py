"""brandkit.name nullable — derive display from scope parent

Revision ID: u8j9k0l1m2n3
Revises: t7i8j9k0l1m2
Create Date: 2026-04-17

The `name` and `description` columns on `brandkits` duplicate the parent
scope entity (merchant.name / offer.name). Downstream consumers never read
them in business logic (prompt_builder / MCP agent prompts / repo queries
all ignore them). The frontend now derives display from the parent.

We drop the NOT NULL constraint on `name` so new brandkits can be created
without supplying a redundant name — existing rows preserved unchanged.
`description` was already nullable. Columns stay (no data loss) for API
backward compatibility.
"""
from alembic import op
import sqlalchemy as sa

revision = "u8j9k0l1m2n3"
down_revision = "t7i8j9k0l1m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "brandkits",
        "name",
        existing_type=sa.String(length=255),
        nullable=True,
    )


def downgrade() -> None:
    # Restore NOT NULL. Fill any NULLs with a placeholder first so existing
    # data is still valid under the old constraint.
    op.execute("UPDATE brandkits SET name = 'BrandKit' WHERE name IS NULL")
    op.alter_column(
        "brandkits",
        "name",
        existing_type=sa.String(length=255),
        nullable=False,
    )
