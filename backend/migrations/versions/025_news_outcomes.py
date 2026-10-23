"""news outcomes / source score history (P7a)

``news_outcomes`` is one evaluation per ``news_item_symbols`` row: a price
claim is scored against SPY-relative adjusted bars once its horizon ends, a
missing bar becomes ``no_data`` after a grace period. ``news_source_score_history``
keeps one Beta-posterior score per source per evaluated session, which the
consecutive-low-lift auto-disable rule reads back.

Revision ID: 025
Revises: 024
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '025'
down_revision: Union[str, None] = '024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'news_outcomes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('news_symbol_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('method', sa.String(length=6), nullable=False),
        sa.Column('scoring_eligible', sa.Boolean(), nullable=False),
        sa.Column('anchor_session', sa.Date(), nullable=False),
        sa.Column('end_session', sa.Date(), nullable=False),
        sa.Column('anchor_price', sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column('end_price', sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column('realized_move_pct', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('spy_move_pct', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('excess_move_pct', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('sector_etf', sa.String(length=10), nullable=True),
        sa.Column('sector_move_pct', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('hit', sa.Boolean(), nullable=True),
        sa.Column('score', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('magnitude_error_pct', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('verdict', sa.String(length=14), nullable=True),
        sa.Column('evidence', sa.String(length=300), nullable=True),
        sa.Column('llm_call_id', sa.UUID(), nullable=True),
        sa.Column('reason', sa.String(length=200), nullable=True),
        sa.Column('evaluated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['news_symbol_id'], ['news_item_symbols.id'],
                                name='fk_news_outcome_symbol', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['llm_call_id'], ['llm_calls.id'],
                                name='fk_news_outcome_llm_call', ondelete='SET NULL'),
        sa.UniqueConstraint('news_symbol_id', name='uq_news_outcome_symbol_row'),
        sa.CheckConstraint("status IN ('scored', 'no_data', 'excluded')",
                           name='ck_news_outcome_status'),
        sa.CheckConstraint("method IN ('price', 'llm')", name='ck_news_outcome_method'),
        sa.CheckConstraint("verdict IN ('happened', 'not_happened', 'no_evidence')",
                           name='ck_news_outcome_verdict_value'),
        sa.CheckConstraint("((status = 'scored') = (hit IS NOT NULL)) "
                           "AND ((status = 'scored') = (score IS NOT NULL))",
                           name='ck_news_outcome_scored'),
        sa.CheckConstraint("(method = 'llm' AND status = 'scored') = (verdict IS NOT NULL)",
                           name='ck_news_outcome_verdict'),
        sa.CheckConstraint('score BETWEEN 0 AND 1', name='ck_news_outcome_score'),
        sa.CheckConstraint("(status = 'scored') = (reason IS NULL)",
                           name='ck_news_outcome_reason'),
        sa.CheckConstraint("method <> 'price' OR status <> 'scored' OR "
                           "(anchor_price IS NOT NULL AND end_price IS NOT NULL "
                           "AND realized_move_pct IS NOT NULL AND spy_move_pct IS NOT NULL "
                           "AND excess_move_pct IS NOT NULL AND magnitude_error_pct IS NOT NULL)",
                           name='ck_news_outcome_price'),
    )
    op.create_index('ix_news_outcomes_evaluated', 'news_outcomes', ['evaluated_at'])

    op.create_table(
        'news_source_score_history',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=False),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('n', sa.Integer(), nullable=False),
        sa.Column('hits', sa.Integer(), nullable=False),
        sa.Column('score', sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column('lift', sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column('prior_mean', sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column('brier', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['source_id'], ['news_sources.id'],
                                name='fk_news_source_score_source', ondelete='CASCADE'),
        sa.UniqueConstraint('source_id', 'as_of', name='uq_news_source_score_day'),
        sa.CheckConstraint('n >= 1 AND hits >= 0 AND hits <= n',
                           name='ck_news_source_score_counts'),
    )


def downgrade() -> None:
    op.drop_index('ix_news_outcomes_evaluated', table_name='news_outcomes')
    op.drop_table('news_source_score_history')
    op.drop_table('news_outcomes')
