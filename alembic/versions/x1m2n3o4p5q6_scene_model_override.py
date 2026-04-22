"""Add model_scene_configs.model_name (per-scene model override)

Revision ID: x1m2n3o4p5q6
Revises: w0l1m2n3o4p5
Create Date: 2026-04-22

Decouples "which endpoint to call" (llm_configs row — url + key + default
model) from "which model to ask for at this scene" (model_scene_configs row).
Lets a single endpoint (typical for aggregator proxies: OneAPI / LiteLLM /
OpenRouter / enterprise gateways) serve many models without forcing users to
create one llm_config row per model.

Semantics:
  model_scene_configs.model_name IS NULL → use llm_configs.model_name (default)
  model_scene_configs.model_name IS NOT NULL → use the scene-level override

Backward compatible: existing scene rows have NULL, so runtime behavior is
unchanged until a user picks an override in Settings.
"""
from alembic import op

revision = "x1m2n3o4p5q6"
down_revision = "w0l1m2n3o4p5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE model_scene_configs "
        "ADD COLUMN IF NOT EXISTS model_name VARCHAR(255);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE model_scene_configs "
        "DROP COLUMN IF EXISTS model_name;"
    )
