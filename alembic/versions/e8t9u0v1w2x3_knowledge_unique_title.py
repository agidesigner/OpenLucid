"""Dedup duplicate knowledge_items per (scope, type, title) + enforce uniqueness.

``KnowledgeService.batch_upsert`` has always matched existing rows on the
tuple ``(scope_type, scope_id, knowledge_type, title)`` — but the DB never
enforced that constraint, so concurrent requests or title edits could
silently produce duplicates (two rows that look identical to the service
but diverge on ``content_raw``). A third-party review surfaced this as
"upsert 只按标题去重, 标题变化会产生重复项"; the right fix is to
promote the existing implicit key to an actual DB-level UNIQUE index.

Mirrors migration ``d7s8t9u0v1w2`` (brandkit_unique_scope).

This migration:
1. Per ``(scope_type, scope_id, knowledge_type, title)`` group, keep the
   "richest" row (longest ``content_raw``; tie-break by ``updated_at``
   DESC — newest wins) and delete the rest. ``knowledge_items`` has no
   FK children so no cascade concerns.
2. Adds UNIQUE index ``uq_knowledge_title`` so the same duplicate can
   never reappear.

Downgrade drops the index; it does not resurrect deleted rows — the
"richest" winner already holds the meaningful content.
"""
from alembic import op
import sqlalchemy as sa


revision = "e8t9u0v1w2x3"
down_revision = "d7s8t9u0v1w2"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Step 1 — collapse duplicates to the richest row per key tuple.
    # Richness = LENGTH(content_raw); tie-breaks by updated_at DESC.
    conn.execute(sa.text(
        """
        WITH ranked AS (
          SELECT id,
            ROW_NUMBER() OVER (
              PARTITION BY scope_type, scope_id, knowledge_type, title
              ORDER BY
                LENGTH(COALESCE(content_raw, '')) DESC,
                updated_at DESC
            ) AS rn
          FROM knowledge_items
        )
        DELETE FROM knowledge_items
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        """
    ))

    # Step 2 — enforce uniqueness going forward.
    op.create_index(
        "uq_knowledge_title",
        "knowledge_items",
        ["scope_type", "scope_id", "knowledge_type", "title"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_knowledge_title", table_name="knowledge_items")
    # Deleted duplicate rows are not restored — the winner holds the content.
