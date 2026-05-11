"""Add user_prompt_presets table

Revision ID: 74f213b04bd4
Revises: i2x3y4z5a6b7
Create Date: 2026-05-10 12:20:22.479072

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '74f213b04bd4'
down_revision: Union[str, None] = 'i2x3y4z5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('user_prompt_presets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('preset_key', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=False),
    sa.Column('lang', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_prompt_presets_preset_key'), 'user_prompt_presets', ['preset_key'], unique=False)
    op.create_index(op.f('ix_user_prompt_presets_user_id'), 'user_prompt_presets', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_prompt_presets_user_id'), table_name='user_prompt_presets')
    op.drop_index(op.f('ix_user_prompt_presets_preset_key'), table_name='user_prompt_presets')
    op.drop_table('user_prompt_presets')