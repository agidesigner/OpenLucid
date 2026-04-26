"""Add trend-bridge persistence to topic_plans

Revision ID: f9u0v1w2x3y4
Revises: e8t9u0v1w2x3
Create Date: 2026-04-27

Topic Studio's trend-bridge mode (the "ride a hot topic" feature) emits
two augmentations the LLM produces but the original schema didn't store:

  - the structured hotspot summary (event, keywords, public_attention,
    risk_zones) — shared across all plans in the same generation
  - per-plan tier / risk / do_not_associate guardrails

Without these on the row, the script-writer step (which fetches plans
by id via topic_plan_id) loses the trend context entirely and falls
back to KB-only generation, producing copy that doesn't ride the
trend it was generated for. Symptom: a topic generated against
"DeepSeek V4 launches" becomes a generic product spiel by the time
the script lands.

All four columns are nullable — classic-mode plans (no external
context) leave them empty.

IF NOT EXISTS makes this idempotent for any env where someone
hand-added the columns earlier.
"""
from alembic import op

revision = "f9u0v1w2x3y4"
down_revision = "e8t9u0v1w2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE topic_plans ADD COLUMN IF NOT EXISTS hotspot_json JSONB;")
    op.execute("ALTER TABLE topic_plans ADD COLUMN IF NOT EXISTS do_not_associate_json JSONB;")
    op.execute("ALTER TABLE topic_plans ADD COLUMN IF NOT EXISTS relevance_tier VARCHAR(16);")
    op.execute("ALTER TABLE topic_plans ADD COLUMN IF NOT EXISTS risk_of_forced_relevance DOUBLE PRECISION;")


def downgrade() -> None:
    op.execute("ALTER TABLE topic_plans DROP COLUMN IF EXISTS risk_of_forced_relevance;")
    op.execute("ALTER TABLE topic_plans DROP COLUMN IF EXISTS relevance_tier;")
    op.execute("ALTER TABLE topic_plans DROP COLUMN IF EXISTS do_not_associate_json;")
    op.execute("ALTER TABLE topic_plans DROP COLUMN IF EXISTS hotspot_json;")
