"""add memory_entries

Revision ID: h1w2x3y4z5a6
Revises: g0v1w2x3y4z5
Create Date: 2026-05-06

Memory system (v1.6): persistent user preferences captured during refine
flows (image-studio / content-studio cover) and surfaced
back into every prompt assembly so a single piece of feedback applies
to all future generations.

Schema choices recap (see plans/.../memory.md):
  * (scope_type, scope_id) mirrors the Asset / Knowledge scope model so
    a memory attaches to either a single offer or a whole merchant.
  * `surface` filters which generators consume the memory ("image" /
    "script" / "all"). Cross-cutting brand/generation prefs use "all";
    surface-specific prefs (e.g. "logo bottom-right" for images only)
    use the targeted value.
  * Free-text `content` only — no `kind`/category column. Earlier
    drafts had brand_rule / format_rule / avoid / prefer; the
    distinction added user friction without changing prompt assembly.
  * `source` + `source_ref` retain provenance ("captured from refine
    job X") for audit / future reverse-link UIs without forcing the
    UI to hard-link.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "h1w2x3y4z5a6"
down_revision = "g0v1w2x3y4z5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 'merchant' or 'offer'
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 'all' / 'image' / 'script'
        sa.Column(
            "surface", sa.String(16), nullable=False, server_default="all"
        ),
        sa.Column("content", sa.Text, nullable=False),
        # 'manual' / 'refine_capture' / 'mcp'
        sa.Column(
            "source", sa.String(20), nullable=False, server_default="manual"
        ),
        # When source='refine_capture', stores the parent job_id so the
        # UI can later link "this preference was learned from THAT
        # generation". Free-form text — nothing else reads this column.
        sa.Column("source_ref", sa.Text, nullable=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.true()
        ),
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
    # Composite index: every prompt-assembly read filters by
    # (scope_type, scope_id, is_active) and usually narrows further by
    # surface. Single covering index keeps the hot path one b-tree
    # lookup; merchant_id alone is too coarse.
    op.create_index(
        "ix_memory_entries_scope_active_surface",
        "memory_entries",
        ["scope_type", "scope_id", "is_active", "surface"],
    )
    op.create_index(
        "ix_memory_entries_merchant_id",
        "memory_entries",
        ["merchant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_entries_merchant_id", table_name="memory_entries")
    op.drop_index(
        "ix_memory_entries_scope_active_surface", table_name="memory_entries"
    )
    op.drop_table("memory_entries")
