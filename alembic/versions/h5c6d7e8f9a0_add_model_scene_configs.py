"""replace llm_scene_configs with model_scene_configs

Revision ID: h5c6d7e8f9a0
Revises: f3a4b5c6d7e8
Create Date: 2026-03-28 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h5c6d7e8f9a0"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_scene_configs",
        sa.Column("scene_key", sa.String(100), nullable=False),
        sa.Column("model_type", sa.String(50), nullable=False),
        sa.Column("config_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["config_id"],
            ["llm_configs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("scene_key", "model_type"),
    )

    # Migrate existing data from llm_scene_configs
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT scene, llm_config_id FROM llm_scene_configs WHERE llm_config_id IS NOT NULL")
    ).fetchall()

    SCENE_MAP = {
        "topic": "topic_studio",
        "knowledge": "knowledge",
        "copywriting": "copywriting",
    }

    for scene, config_id in rows:
        new_key = SCENE_MAP.get(scene, scene)
        conn.execute(
            sa.text(
                "INSERT INTO model_scene_configs (scene_key, model_type, config_id) "
                "VALUES (:scene_key, :model_type, :config_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"scene_key": new_key, "model_type": "text_llm", "config_id": str(config_id)},
        )

    op.drop_table("llm_scene_configs")


def downgrade() -> None:
    op.create_table(
        "llm_scene_configs",
        sa.Column("scene", sa.String(50), nullable=False),
        sa.Column("llm_config_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["llm_config_id"], ["llm_configs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("scene"),
    )
    op.drop_table("model_scene_configs")
