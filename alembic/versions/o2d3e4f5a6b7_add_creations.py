"""add creations table

Revision ID: o2d3e4f5a6b7
Revises: n1c2d3e4f5a6
Create Date: 2026-04-09
"""
from alembic import op
import sqlalchemy as sa

revision = "o2d3e4f5a6b7"
down_revision = "n1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creations",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("offer_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False, server_default="general"),
        sa.Column("tags", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("source_app", sa.String(80), nullable=False, server_default="manual"),
        sa.Column("source_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("creations")
