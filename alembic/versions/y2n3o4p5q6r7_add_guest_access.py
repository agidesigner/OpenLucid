"""Add guest_access table

Revision ID: y2n3o4p5q6r7
Revises: x1m2n3o4p5q6
Create Date: 2026-04-23

Singleton table (service layer enforces max one row) that records the sha256
hash of the current guest-mode shareable token. When the row exists, the
owner has enabled guest mode and visitors presenting the matching token (via
cookie or the /guest-access?t=... portal URL) can use the WebUI in read-only
+ content-creation mode.
"""
from alembic import op
import sqlalchemy as sa

revision = "y2n3o4p5q6r7"
down_revision = "x1m2n3o4p5q6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_access",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("guest_access")
