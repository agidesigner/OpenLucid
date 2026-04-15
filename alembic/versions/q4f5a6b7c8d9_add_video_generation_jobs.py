"""add video_generation_jobs table

Revision ID: q4f5a6b7c8d9
Revises: p3e4f5a6b7c8
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = "q4f5a6b7c8d9"
down_revision = "p3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_generation_jobs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column(
            "provider_config_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_provider_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider_task_id", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("params", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("video_url", sa.Text, nullable=True),
        sa.Column("cover_url", sa.Text, nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("progress", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        "idx_vgj_creation", "video_generation_jobs", ["creation_id"]
    )
    op.create_index("idx_vgj_status", "video_generation_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("idx_vgj_status", table_name="video_generation_jobs")
    op.drop_index("idx_vgj_creation", table_name="video_generation_jobs")
    op.drop_table("video_generation_jobs")
