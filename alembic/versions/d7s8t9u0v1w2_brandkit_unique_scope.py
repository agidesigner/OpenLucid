"""Dedup duplicate brandkits per scope + enforce uniqueness going forward.

Revision ID: d7s8t9u0v1w2
Revises: c6r7s8t9u0v1
Create Date: 2026-04-24

``BrandKitService.create`` has always asserted "one brand kit per merchant
scope" at the application layer, but early versions skipped that check and
left some merchants with two rows. That collision turned the video_service's
new reference-image lookup into a ``MultipleResultsFound`` exception,
swallowing the entire reference-image path (B-roll submitted with no image
guidance, visuals drift from the real product).

This migration:
1. Computes a "richness" score per brandkit (assets > colors/fonts > voice
   length), keeps the top-scoring row per ``(scope_type, scope_id)``, and
   deletes the rest. CASCADE FKs on brandkit_colors/fonts/asset_links clean
   child rows automatically.
2. Adds a UNIQUE index on ``(scope_type, scope_id)`` so the application
   guarantee is enforced at the DB level — the same duplicate can never
   happen again even if a future bug slips past the service check.

Picking the "richest" row (rather than most-recent) matches how users
actually work: they abandon a first draft ("公司品牌规范") and build on the
one they're iterating on ("111" with 4 assets / 4 colors / 2 fonts).
Keeping the empty draft and deleting the richer one would be the worst
possible outcome.
"""
from alembic import op
import sqlalchemy as sa


revision = "d7s8t9u0v1w2"
down_revision = "c6r7s8t9u0v1"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Step 1 — find duplicate scopes + pick the winner per scope.
    # Richness score weights:
    #   each asset_link → 1000 (highest signal; explicit upload work)
    #   each color → 100
    #   each font → 100
    #   each char in brand_voice → 1 (text is cheap; presence is what matters)
    # Ties break by updated_at DESC (newest wins).
    conn.execute(sa.text(
        """
        WITH scored AS (
          SELECT b.id, b.scope_type, b.scope_id, b.updated_at,
            LENGTH(COALESCE(b.brand_voice, '')) AS voice_chars,
            COALESCE((SELECT COUNT(*) FROM brandkit_asset_links l WHERE l.brandkit_id = b.id), 0) AS asset_count,
            COALESCE((SELECT COUNT(*) FROM brandkit_colors c WHERE c.brandkit_id = b.id), 0) AS color_count,
            COALESCE((SELECT COUNT(*) FROM brandkit_fonts f WHERE f.brandkit_id = b.id), 0) AS font_count
          FROM brandkits b
        ),
        ranked AS (
          SELECT id, scope_type, scope_id,
            ROW_NUMBER() OVER (
              PARTITION BY scope_type, scope_id
              ORDER BY
                (asset_count * 1000 + color_count * 100 + font_count * 100 + voice_chars) DESC,
                updated_at DESC
            ) AS rn
          FROM scored
        )
        DELETE FROM brandkits
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        """
    ))

    # Step 2 — enforce uniqueness going forward.
    op.create_index(
        "uq_brandkit_scope",
        "brandkits",
        ["scope_type", "scope_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_brandkit_scope", table_name="brandkits")
    # Deleted duplicate rows are not restored on downgrade — the "richer"
    # winner already holds the meaningful content.
