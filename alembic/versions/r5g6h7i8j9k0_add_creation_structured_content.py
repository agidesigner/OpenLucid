"""add structured_content to creations

Revision ID: r5g6h7i8j9k0
Revises: q4f5a6b7c8d9
Create Date: 2026-04-12
"""
from alembic import op
import sqlalchemy as sa

revision = "r5g6h7i8j9k0"
down_revision = "q4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "creations",
        sa.Column("structured_content", sa.dialects.postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("creations", "structured_content")
