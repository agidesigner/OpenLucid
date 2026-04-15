"""add media_provider_configs table

Revision ID: p3e4f5a6b7c8
Revises: o2d3e4f5a6b7
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa

revision = "p3e4f5a6b7c8"
down_revision = "o2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_provider_configs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),  # 'chanjing' | 'jogg'
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column(
            "credentials",
            sa.dialects.postgresql.JSONB,
            nullable=False,
        ),  # chanjing: {app_id, secret_key}; jogg: {api_key}
        sa.Column(
            "defaults",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),  # {avatar_id, voice_id, aspect_ratio}
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_mpc_provider", "media_provider_configs", ["provider"]
    )


def downgrade() -> None:
    op.drop_index("idx_mpc_provider", table_name="media_provider_configs")
    op.drop_table("media_provider_configs")
