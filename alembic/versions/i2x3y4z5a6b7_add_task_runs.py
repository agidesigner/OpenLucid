"""add task_runs

Revision ID: i2x3y4z5a6b7
Revises: h1w2x3y4z5a6
Create Date: 2026-05-09

Durable application task runs for request-detached work such as asset parsing
and derived offer-model inference.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "i2x3y4z5a6b7"
down_revision = "h1w2x3y4z5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_type", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unique_key", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("progress", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("locked_by", sa.String(128), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_task_runs_task_type", "task_runs", ["task_type"])
    op.create_index("ix_task_runs_entity_type", "task_runs", ["entity_type"])
    op.create_index("ix_task_runs_entity_id", "task_runs", ["entity_id"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_status_created_at", "task_runs", ["status", "created_at"])
    op.create_index("ix_task_runs_task_type_status", "task_runs", ["task_type", "status"])
    op.create_index(
        "uq_task_runs_active_unique_key",
        "task_runs",
        ["unique_key"],
        unique=True,
        postgresql_where=sa.text("unique_key IS NOT NULL AND status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_task_runs_active_unique_key", table_name="task_runs")
    op.drop_index("ix_task_runs_task_type_status", table_name="task_runs")
    op.drop_index("ix_task_runs_status_created_at", table_name="task_runs")
    op.drop_index("ix_task_runs_status", table_name="task_runs")
    op.drop_index("ix_task_runs_entity_id", table_name="task_runs")
    op.drop_index("ix_task_runs_entity_type", table_name="task_runs")
    op.drop_index("ix_task_runs_task_type", table_name="task_runs")
    op.drop_table("task_runs")
