"""assets module v2: processing jobs, metrics, title/content_text

Revision ID: i6d7e8f9a0b1
Revises: h5c6d7e8f9a0
Create Date: 2026-03-28 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "i6d7e8f9a0b1"
down_revision: Union[str, None] = "h5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add title and content_text to assets table (IF NOT EXISTS — idempotent)
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS title VARCHAR(512)")
    op.execute("ALTER TABLE assets ADD COLUMN IF NOT EXISTS content_text TEXT")

    # GIN index on tags_json for fast JSONB containment queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_assets_tags_gin
        ON assets USING gin (tags_json)
    """)

    # asset_processing_jobs table
    op.create_table(
        "asset_processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_asset_processing_jobs_asset_id", "asset_processing_jobs", ["asset_id"])

    # asset_metrics table
    op.create_table(
        "asset_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric_type", sa.String(64), nullable=False),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ref_type", sa.String(64), nullable=True),
    )
    op.create_index("ix_asset_metrics_asset_id", "asset_metrics", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_asset_metrics_asset_id", table_name="asset_metrics")
    op.drop_table("asset_metrics")
    op.drop_index("ix_asset_processing_jobs_asset_id", table_name="asset_processing_jobs")
    op.drop_table("asset_processing_jobs")
    op.drop_index("ix_assets_tags_gin", table_name="assets")
    op.drop_column("assets", "content_text")
    op.drop_column("assets", "title")
