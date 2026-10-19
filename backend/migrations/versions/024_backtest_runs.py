"""backtest runs (P3b): queued single / walk-forward backtests of a group

The API inserts a ``queued`` row and the research worker claims it with
``FOR UPDATE SKIP LOCKED``, so the table is the whole hand-off between the
backend and Celery. ``group_id`` is RESTRICT (a group with runs cannot be
deleted); ``created_by`` is SET NULL. A terminal status always carries
``finished_at`` and only a succeeded run carries ``metrics_json``.

Revision ID: 024
Revises: 023
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '024'
down_revision: Union[str, None] = '023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'backtest_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='queued', nullable=False),
        sa.Column('config_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('config_hash', sa.String(length=64), nullable=False),
        sa.Column('data_start', sa.Date(), nullable=False),
        sa.Column('data_end', sa.Date(), nullable=False),
        sa.Column('progress', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('cancel_requested', sa.Boolean(), server_default=sa.text('false'),
                  nullable=False),
        sa.Column('metrics_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('calibration_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('artifact_path', sa.Text(), nullable=True),
        sa.Column('universe_label', sa.String(length=40), nullable=False),
        sa.Column('survivorship_biased', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['strategy_groups.id'],
                                name='fk_backtest_run_group', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'],
                                name='fk_backtest_run_user', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("kind IN ('single', 'walk_forward')", name='ck_backtest_run_kind'),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name='ck_backtest_run_status'),
        sa.CheckConstraint('data_start < data_end', name='ck_backtest_run_range'),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name='ck_backtest_run_finished'),
        sa.CheckConstraint("(status = 'succeeded') = (metrics_json IS NOT NULL)",
                           name='ck_backtest_run_metrics'),
    )
    op.create_index('ix_backtest_runs_group_created', 'backtest_runs',
                    ['group_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_backtest_runs_group_created', table_name='backtest_runs')
    op.drop_table('backtest_runs')
