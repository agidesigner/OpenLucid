"""add last_used_at to mcp_tokens

Revision ID: t7i8j9k0l1m2
Revises: s6h7i8j9k0l1
Create Date: 2026-04-16

Adds a nullable `last_used_at` timestamp that `_mcp_token_check` updates on
every successful token match. Surfaces "Connected Agents" visibility in
Settings → MCP.
"""
from alembic import op
import sqlalchemy as sa

revision = "t7i8j9k0l1m2"
down_revision = "s6h7i8j9k0l1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_tokens",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mcp_tokens", "last_used_at")
