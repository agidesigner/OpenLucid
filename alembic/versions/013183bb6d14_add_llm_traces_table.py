"""add_llm_traces_table

Revision ID: 013183bb6d14
Revises: c4fb18432be2
Create Date: 2026-05-10 14:53:23.050281

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '013183bb6d14'
down_revision: Union[str, None] = 'c4fb18432be2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_traces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        
        # Call identity
        sa.Column('scene_key', sa.String(80), nullable=True),
        sa.Column('call_type', sa.String(32), nullable=False),
        sa.Column('model_name', sa.String(200), nullable=False),
        sa.Column('provider', sa.String(80), nullable=False, server_default='unknown'),
        
        # Input
        sa.Column('system_prompt', sa.Text, nullable=True),
        sa.Column('user_prompt', sa.Text, nullable=True),
        sa.Column('extra_params', postgresql.JSONB, nullable=True),
        
        # Output
        sa.Column('response_text', sa.Text, nullable=True),
        sa.Column('thinking_text', sa.Text, nullable=True),
        sa.Column('tool_calls', postgresql.JSONB, nullable=True),
        
        # Metrics
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('latency_ms', sa.Integer, nullable=True),
        sa.Column('prompt_tokens', sa.Integer, nullable=True),
        sa.Column('completion_tokens', sa.Integer, nullable=True),
        sa.Column('total_tokens', sa.Integer, nullable=True),
        
        # Associations
        sa.Column('request_id', sa.String(255), nullable=True),
        sa.Column('entity_type', sa.String(80), nullable=True),
        sa.Column('entity_id', postgresql.UUID(as_uuid=True), nullable=True),
        
        # Timing
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    
    # Create indexes
    op.create_index('ix_llm_traces_scene_key', 'llm_traces', ['scene_key'])
    op.create_index('ix_llm_traces_status', 'llm_traces', ['status'])
    op.create_index('ix_llm_traces_scene_created', 'llm_traces', ['scene_key', 'created_at'])
    op.create_index('ix_llm_traces_status_created', 'llm_traces', ['status', 'created_at'])
    op.create_index('ix_llm_traces_created_at', 'llm_traces', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_llm_traces_created_at', 'llm_traces')
    op.drop_index('ix_llm_traces_status_created', 'llm_traces')
    op.drop_index('ix_llm_traces_scene_created', 'llm_traces')
    op.drop_index('ix_llm_traces_status', 'llm_traces')
    op.drop_index('ix_llm_traces_scene_key', 'llm_traces')
    op.drop_table('llm_traces')
