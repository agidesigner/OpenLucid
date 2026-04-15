"""add media_capability_defaults table

Revision ID: s6h7i8j9k0l1
Revises: r5g6h7i8j9k0
Create Date: 2026-04-14

Stores the default provider + model for non-LLM capabilities
(image_gen, video_gen, tts). One row per capability (primary key).
"""
from alembic import op
import sqlalchemy as sa

revision = "s6h7i8j9k0l1"
down_revision = "r5g6h7i8j9k0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_capability_defaults",
        sa.Column("capability", sa.String(32), primary_key=True),
        sa.Column(
            "provider_config_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_provider_configs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("model_code", sa.String(128), nullable=True),
        sa.Column("voice_id", sa.String(128), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("media_capability_defaults")
