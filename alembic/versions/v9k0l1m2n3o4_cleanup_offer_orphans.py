"""One-shot cleanup of rows pointing to deleted offers

Revision ID: v9k0l1m2n3o4
Revises: u8j9k0l1m2n3
Create Date: 2026-04-21

Prior to `OfferService.delete()` cascading dependent rows, hard-deleting an
offer left orphans in five tables (no FK + polymorphic scope pattern):

    table                       orphan rows in prod at time of this fix
    knowledge_items (offer)            284
    topic_plans                         45
    creations (offer-linked)            20
    assets (offer)                       2
    brandkits (offer)                    1
    ---
    total                              352

These rows were never visible in any product surface (every query joins
through offers.id which no longer resolves) — they were dead weight.

This migration removes them. Safe because:
 - no live UI / API path reads them
 - the offer they belonged to is already gone
 - strategy_units already cascades via SQL FK, hence not listed

Asset file cleanup on disk is *not* attempted (only 2 rows, bytes are small,
and doing safe file I/O from a DB migration is risky).
"""
from alembic import op

revision = "v9k0l1m2n3o4"
down_revision = "u8j9k0l1m2n3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All five statements are idempotent — rerunning is a no-op once orphans
    # are gone. Using "NOT EXISTS" rather than hard-coded IDs keeps this safe
    # across environments (dev / staging / prod each have different orphans).
    op.execute("""
        DELETE FROM knowledge_items
        WHERE scope_type = 'offer'
          AND NOT EXISTS (SELECT 1 FROM offers WHERE offers.id = knowledge_items.scope_id);
    """)
    op.execute("""
        DELETE FROM brandkits
        WHERE scope_type = 'offer'
          AND NOT EXISTS (SELECT 1 FROM offers WHERE offers.id = brandkits.scope_id);
    """)
    op.execute("""
        DELETE FROM assets
        WHERE scope_type = 'offer'
          AND NOT EXISTS (SELECT 1 FROM offers WHERE offers.id = assets.scope_id);
    """)
    op.execute("""
        DELETE FROM topic_plans
        WHERE NOT EXISTS (SELECT 1 FROM offers WHERE offers.id = topic_plans.offer_id);
    """)
    op.execute("""
        DELETE FROM creations
        WHERE offer_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM offers WHERE offers.id = creations.offer_id);
    """)


def downgrade() -> None:
    # Data is already gone and can't be reconstructed — downgrade is a no-op
    # rather than a fake promise. Restore from backup if needed.
    pass
