"""Add raw_token to guest_access so the owner can view + copy the share
URL anytime, not just once at creation.

Revision ID: a4p5q6r7s8t9
Revises: z3o4p5q6r7s8
Create Date: 2026-04-23

Trade-off: stores the raw share token in plaintext alongside its hash.
The token grants guest (read + narrow-write) access only; regenerating
invalidates it instantly. Security model matches the existing llm_configs
table, which also stores API keys in plaintext — this is a self-hosted
single-user product where DB access = root access by assumption.

Existing rows from before this migration have no raw token stored.
``raw_token`` is nullable so they survive the migration; the UI falls
back to "regenerate to reveal a new URL" for them.
"""
from alembic import op
import sqlalchemy as sa

revision = "a4p5q6r7s8t9"
down_revision = "z3o4p5q6r7s8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE guest_access "
        "ADD COLUMN IF NOT EXISTS raw_token VARCHAR(128);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE guest_access "
        "DROP COLUMN IF EXISTS raw_token;"
    )
