"""Reshape brandkits to HeyGen-style primitives: colors + fonts + brand_voice.

Revision ID: b5q6r7s8t9u0
Revises: a4p5q6r7s8t9
Create Date: 2026-04-23

The original seven JSONB fields (style_profile_json, product_visual_profile_json,
service_scene_profile_json, persona_profile_json, visual_do_json, visual_dont_json,
reference_prompt_json) were never consumed by any downstream LLM call — not
script_composer, topic_plan_service, script_writer_service, kb_qa_service, nor
any MCP tool. They existed as disconnected metadata, which is why the "AI Quick
Fill" feature that tried to populate them never produced anything useful.

HeyGen (and every other real video-gen product we audited) treats brand as
structured primitives + reference images, not descriptive JSON. We follow.

New shape:

1. ``brandkits.brand_voice`` — single text column. This is the ONE field
   that text generation actually reads (plumbed into script_composer's
   BRAND layer in the next commit).
2. ``brandkit_colors`` — hex codes with a role enum (primary/secondary/
   tertiary/accent/custom). Structured so image generation later can pick
   by role without parsing.
3. ``brandkit_fonts`` — typefaces with a role enum (title/body). Named
   only; storing a font file is deferred until we actually render text.
4. ``brandkit_asset_links.role`` keeps its existing varchar(32) column —
   the taxonomy (logo / reference_sample / product_reference / other) is
   enforced at the schema layer, not the DB.

Data preservation: existing JSONB text content is concatenated into
``brand_voice`` so no user-authored content is lost. Users can then edit
down to the tight voice description the new field expects.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b5q6r7s8t9u0"
down_revision = "a4p5q6r7s8t9"
branch_labels = None
depends_on = None


def upgrade():
    # ── brand_voice column ───────────────────────────────────────
    op.add_column(
        "brandkits",
        sa.Column("brand_voice", sa.Text(), nullable=True),
    )

    # Concatenate existing JSONB text into brand_voice seed. jsonb_typeof
    # filters non-string values (numbers, objects) out — the original
    # fields were all meant to be free-text strings, but we defend
    # against anything malformed. Only non-null values are joined.
    op.execute(
        """
        UPDATE brandkits SET brand_voice = NULLIF(
            concat_ws(
                E'\n\n',
                CASE WHEN style_profile_json IS NOT NULL
                    AND jsonb_typeof(style_profile_json) = 'string'
                    THEN '【品牌气质】' || (style_profile_json #>> '{}') END,
                CASE WHEN product_visual_profile_json IS NOT NULL
                    AND jsonb_typeof(product_visual_profile_json) = 'string'
                    THEN '【产品视觉】' || (product_visual_profile_json #>> '{}') END,
                CASE WHEN service_scene_profile_json IS NOT NULL
                    AND jsonb_typeof(service_scene_profile_json) = 'string'
                    THEN '【服务场景】' || (service_scene_profile_json #>> '{}') END,
                CASE WHEN persona_profile_json IS NOT NULL
                    AND jsonb_typeof(persona_profile_json) = 'string'
                    THEN '【人物角色】' || (persona_profile_json #>> '{}') END,
                CASE WHEN visual_do_json IS NOT NULL
                    AND jsonb_typeof(visual_do_json) = 'string'
                    THEN '【推荐表达】' || (visual_do_json #>> '{}') END,
                CASE WHEN visual_dont_json IS NOT NULL
                    AND jsonb_typeof(visual_dont_json) = 'string'
                    THEN '【避免表达】' || (visual_dont_json #>> '{}') END,
                CASE WHEN reference_prompt_json IS NOT NULL
                    AND jsonb_typeof(reference_prompt_json) = 'string'
                    THEN '【参考 Prompt】' || (reference_prompt_json #>> '{}') END
            ),
            ''
        );
        """
    )

    # ── brandkit_colors ─────────────────────────────────────────
    op.create_table(
        "brandkit_colors",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brandkit_id", sa.UUID(), sa.ForeignKey("brandkits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),   # primary / secondary / tertiary / accent / custom
        sa.Column("hex", sa.String(9), nullable=False),     # #RRGGBB or #RRGGBBAA
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_brandkit_colors_brandkit_id", "brandkit_colors", ["brandkit_id"])

    # ── brandkit_fonts ──────────────────────────────────────────
    op.create_table(
        "brandkit_fonts",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("brandkit_id", sa.UUID(), sa.ForeignKey("brandkits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),       # title / body / custom
        sa.Column("font_name", sa.String(255), nullable=False), # e.g. "Alice", "Roboto Slab"
        sa.Column("font_url", sa.Text(), nullable=True),        # optional: self-hosted webfont
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_brandkit_fonts_brandkit_id", "brandkit_fonts", ["brandkit_id"])

    # ── drop the seven legacy JSONB fields ───────────────────────
    op.drop_column("brandkits", "style_profile_json")
    op.drop_column("brandkits", "product_visual_profile_json")
    op.drop_column("brandkits", "service_scene_profile_json")
    op.drop_column("brandkits", "persona_profile_json")
    op.drop_column("brandkits", "visual_do_json")
    op.drop_column("brandkits", "visual_dont_json")
    op.drop_column("brandkits", "reference_prompt_json")


def downgrade():
    # Best-effort restore: re-add the seven JSONB columns as null. Match
    # the ORIGINAL jsonb type (not plain ``json``) so an upgrade re-run
    # after downgrade won't trip on ``jsonb_typeof()`` being absent for
    # the ``json`` type. Seed data in ``brand_voice`` cannot be split
    # back into seven semantic buckets, so downgrade is non-round-trip
    # for content — just for schema.
    from sqlalchemy.dialects.postgresql import JSONB
    op.add_column("brandkits", sa.Column("style_profile_json", JSONB(), nullable=True))
    op.add_column("brandkits", sa.Column("product_visual_profile_json", JSONB(), nullable=True))
    op.add_column("brandkits", sa.Column("service_scene_profile_json", JSONB(), nullable=True))
    op.add_column("brandkits", sa.Column("persona_profile_json", JSONB(), nullable=True))
    op.add_column("brandkits", sa.Column("visual_do_json", JSONB(), nullable=True))
    op.add_column("brandkits", sa.Column("visual_dont_json", JSONB(), nullable=True))
    op.add_column("brandkits", sa.Column("reference_prompt_json", JSONB(), nullable=True))

    op.drop_index("ix_brandkit_fonts_brandkit_id", table_name="brandkit_fonts")
    op.drop_table("brandkit_fonts")
    op.drop_index("ix_brandkit_colors_brandkit_id", table_name="brandkit_colors")
    op.drop_table("brandkit_colors")
    op.drop_column("brandkits", "brand_voice")
