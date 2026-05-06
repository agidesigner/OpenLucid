"""add image_generation_jobs + cover_image_url + style_anchor_json

Revision ID: g0v1w2x3y4z5
Revises: f9u0v1w2x3y4
Create Date: 2026-05-05

Foundation for the image-generation module (v1.5.0):

  * image_generation_jobs       — async job rows for poster + article-cover modes.
                                  ``mode`` discriminates between the two flows.
  * creations.cover_image_url   — destination URL written when an article-cover
                                  job completes. Posters do NOT write to creations
                                  (they're standalone, not "saved content").
  * brandkits.style_anchor_json — cache of vision-LLM style summaries keyed by
                                  selling_point. Avoids re-billing the vision call
                                  on every poster render of the same offer/topic.

Per the plan (image_generation module): brand-kit color/font tables stay empty
in real-world data; the leverage is reference-poster style extraction, cached
on the brandkit row directly.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g0v1w2x3y4z5"
down_revision = "f9u0v1w2x3y4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # 'poster' uses template + offer + brandkit; 'article_cover' uses creation
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column(
            "creation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "offer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "brandkit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brandkits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("template_id", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column(
            "provider_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_provider_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider_task_id", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("image_url", sa.Text, nullable=True),
        sa.Column("preview_url", sa.Text, nullable=True),
        sa.Column("progress", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("idx_igj_creation", "image_generation_jobs", ["creation_id"])
    op.create_index("idx_igj_offer", "image_generation_jobs", ["offer_id"])
    op.create_index("idx_igj_status", "image_generation_jobs", ["status"])

    # Article-cover destination column. Posters store their URL on the job
    # row only (they're not "saved" content); article covers are persisted
    # back to the parent creation so content-studio can render the cover.
    op.add_column(
        "creations",
        sa.Column("cover_image_url", sa.Text, nullable=True),
    )

    # Style-anchor cache. Keyed by selling_point text inside the JSON to
    # avoid one vision-LLM call per poster render. Invalidated externally
    # by the style_extractor when reference posters change.
    op.add_column(
        "brandkits",
        sa.Column("style_anchor_json", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("brandkits", "style_anchor_json")
    op.drop_column("creations", "cover_image_url")
    op.drop_index("idx_igj_status", table_name="image_generation_jobs")
    op.drop_index("idx_igj_offer", table_name="image_generation_jobs")
    op.drop_index("idx_igj_creation", table_name="image_generation_jobs")
    op.drop_table("image_generation_jobs")
