"""strategy group runs (P3a): one row per group per cycle run

The shared heartbeats keep one row per cycle, so a group that crashed, failed
closed or was skipped inside a multi-group run leaves no trace of its own.
``strategy_group_runs`` records each group's outcome per signal / entry /
position run; ``group_id`` is RESTRICT so a group with history cannot be
deleted.

Revision ID: 023
Revises: 022
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '023'
down_revision: Union[str, None] = '022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'strategy_group_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('cycle', sa.String(length=20), nullable=False),
        sa.Column('session_date', sa.Date(), nullable=False),
        sa.Column('seq', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['strategy_groups.id'],
                                name='fk_group_run_group', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("cycle IN ('signal', 'entry', 'position')",
                           name='ck_group_run_cycle'),
        sa.CheckConstraint("outcome IN ('ok', 'skipped', 'blocked', 'fail_closed', 'error')",
                           name='ck_group_run_outcome'),
    )
    op.create_index('ix_strategy_group_runs_group_created', 'strategy_group_runs',
                    ['group_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_strategy_group_runs_group_created', table_name='strategy_group_runs')
    op.drop_table('strategy_group_runs')
