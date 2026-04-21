"""Add topic_plans.user_rating

Revision ID: w0l1m2n3o4p5
Revises: v9k0l1m2n3o4
Create Date: 2026-04-22

Backfills a missing schema change: commit a832ac0 (Apr 1, "topic studio
improvements") added `user_rating` to the TopicPlan ORM model and wired it
into the 生成选题 flow (repo.list_rated filters WHERE user_rating = ±1) but
shipped no Alembic migration. Users who installed fresh after that commit
and rely on `alembic upgrade head` never got the column. Generating topics
then fails with asyncpg UndefinedColumnError → the toast the user sees
reads "字段 topic_plans.user_rating 不存在" (truncated to "topic_pl...").

The column is nullable and unrated by default, so adding it is always safe.
IF NOT EXISTS makes this idempotent for any environment where the column
was manually added earlier.
"""
from alembic import op

revision = "w0l1m2n3o4p5"
down_revision = "v9k0l1m2n3o4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE topic_plans ADD COLUMN IF NOT EXISTS user_rating INTEGER;")


def downgrade() -> None:
    op.execute("ALTER TABLE topic_plans DROP COLUMN IF EXISTS user_rating;")
