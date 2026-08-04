"""llm usage log + recommendation explanation column (2026-08-04)

Adds the ``llm_calls`` usage-log table (every LLM call the system makes is
booked here — platform/model/tokens/snapshotted unit prices/USD cost) and a
nullable ``llm_explanation`` column on ``recommendations`` for the v3
explanation-layer output.

Revision ID: 014
Revises: 013
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '014'
down_revision: Union[str, None] = '013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_calls',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('context', sa.String(length=80), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=True),
        sa.Column('platform', sa.String(length=30), nullable=False),
        sa.Column('model', sa.String(length=60), nullable=False),
        sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cache_read_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cache_creation_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('unit_price_in', sa.Numeric(precision=10, scale=4),
                  server_default='0', nullable=False),
        sa.Column('unit_price_out', sa.Numeric(precision=10, scale=4),
                  server_default='0', nullable=False),
        sa.Column('cost_usd', sa.Numeric(precision=14, scale=8),
                  server_default='0', nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('success', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_llm_calls_created_at'), 'llm_calls',
                    ['created_at'], unique=False)
    op.create_index(op.f('ix_llm_calls_symbol'), 'llm_calls',
                    ['symbol'], unique=False)

    op.add_column('recommendations',
                  sa.Column('llm_explanation', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('recommendations', 'llm_explanation')
    op.drop_index(op.f('ix_llm_calls_symbol'), table_name='llm_calls')
    op.drop_index(op.f('ix_llm_calls_created_at'), table_name='llm_calls')
    op.drop_table('llm_calls')
