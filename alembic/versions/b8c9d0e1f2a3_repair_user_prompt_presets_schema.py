"""repair user_prompt_presets schema drift

Revision ID: b8c9d0e1f2a3
Revises: 013183bb6d14
Create Date: 2026-05-18 10:40:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "013183bb6d14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Some long-running self-hosted volumes reached a state where application
    # code queried user_prompt_presets but the table was absent. Keep this
    # migration idempotent so healthy installs simply no-op.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_prompt_presets (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            preset_key VARCHAR(128) NOT NULL,
            title VARCHAR(256) NOT NULL,
            category VARCHAR(64) NOT NULL,
            lang VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_prompt_presets_user_id "
        "ON user_prompt_presets (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_prompt_presets_preset_key "
        "ON user_prompt_presets (preset_key)"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_user_prompt_preset_user_key'
                  AND conrelid = 'user_prompt_presets'::regclass
            ) THEN
                ALTER TABLE user_prompt_presets
                ADD CONSTRAINT uq_user_prompt_preset_user_key
                UNIQUE (user_id, preset_key);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Repair-only migration: do not drop user data on downgrade.
    pass
