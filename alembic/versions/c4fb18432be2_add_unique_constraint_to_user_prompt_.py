"""add_unique_constraint_to_user_prompt_presets

Revision ID: c4fb18432be2
Revises: 74f213b04bd4
Create Date: 2026-05-10 12:33:17.693763

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4fb18432be2'
down_revision: Union[str, None] = '74f213b04bd4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add unique constraint on (user_id, preset_key)
    op.create_unique_constraint(
        'uq_user_prompt_preset_user_key',
        'user_prompt_presets',
        ['user_id', 'preset_key']
    )


def downgrade() -> None:
    # Drop unique constraint
    op.drop_constraint(
        'uq_user_prompt_preset_user_key',
        'user_prompt_presets',
        type_='unique'
    )
