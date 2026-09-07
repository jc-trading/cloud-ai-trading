"""recommendation forward outcomes (P1 backfill, 2026-08-12)

Adds ``recommendation_outcomes`` — one row per evaluable recommendation with
forward returns measured from the trade_date session open to the close of the
1st / 3rd / 5th following session (same Parquet daily bars the engine uses,
adjusted on read). Derived analytics for the confidence-IC / 复盘 ground work;
written ONLY by the manual backfill module
(``app.modules.simledger.outcomes``), never by the trading cycles.

Revision ID: 015
Revises: 014
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '015'
down_revision: Union[str, None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'recommendation_outcomes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('recommendation_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        # open of the trade_date session (adjusted) — the price a user acting
        # on the rec at the next open would roughly see; all rets anchor here
        sa.Column('base_open', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('close_d0', sa.Numeric(precision=18, scale=6), nullable=False),
        # close(t+N sessions) / base_open - 1; NULL until enough bars exist
        sa.Column('ret_1d', sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column('ret_3d', sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column('ret_5d', sa.Numeric(precision=12, scale=6), nullable=True),
        # last session present in the bars store when this row was computed —
        # a re-run only rewrites rows whose horizons can now be extended
        sa.Column('evaluated_through', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('recommendation_id',
                            name='uq_recommendation_outcome_rec'),
    )
    op.create_index(op.f('ix_recommendation_outcomes_symbol'),
                    'recommendation_outcomes', ['symbol'], unique=False)
    op.create_index(op.f('ix_recommendation_outcomes_trade_date'),
                    'recommendation_outcomes', ['trade_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_recommendation_outcomes_trade_date'),
                  table_name='recommendation_outcomes')
    op.drop_index(op.f('ix_recommendation_outcomes_symbol'),
                  table_name='recommendation_outcomes')
    op.drop_table('recommendation_outcomes')
