"""Create fundamentals cache tables (company_fundamentals + earnings_calendar).

Phase 3 FA cache layer (see merge plan): two local tables that hold the
slow-moving company data so the equity pipeline reads Postgres instead of
burning the limited Finnhub free-tier quota every cycle. Modeled on the
market_data OHLCV cache (003): a plain symbol-keyed cache with a refresh
timestamp.

  - company_fundamentals : one snapshot row per symbol (symbol UNIQUE) —
    profile + shares/S&P membership/avg volume/market cap, historical quarterly
    financials (JSONB), and next-quarter EPS/revenue estimate.
  - earnings_calendar : one row per (symbol, report_date) with a UNIQUE
    constraint so a re-fetch upserts the same event rather than duplicating it.

Fast-changing data (live price / reaction) is fetched live, NOT cached here.

Revision ID: 011
Revises: 010
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = '011'
down_revision = '010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # company_fundamentals: one cached snapshot per symbol.
    op.create_table(
        'company_fundamentals',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('industry', sa.String(255), nullable=True),
        sa.Column('sector', sa.String(255), nullable=True),
        sa.Column('shares_outstanding', sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column('is_sp500', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('avg_volume', sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column('market_cap', sa.Numeric(precision=28, scale=2), nullable=True),
        sa.Column('historical_financials', JSONB(), nullable=True),
        sa.Column('next_quarter_eps_estimate', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('next_quarter_revenue_estimate', sa.Numeric(precision=28, scale=2), nullable=True),
        sa.Column('last_refreshed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symbol', name='uq_company_fundamentals_symbol'),
    )
    op.create_index('ix_company_fundamentals_symbol', 'company_fundamentals', ['symbol'])

    # earnings_calendar: one row per (symbol, report_date).
    op.create_table(
        'earnings_calendar',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('time', sa.String(10), nullable=True),  # bmo / amc
        sa.Column('eps_estimate', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('rev_estimate', sa.Numeric(precision=28, scale=2), nullable=True),
        sa.Column('eps_actual', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('rev_actual', sa.Numeric(precision=28, scale=2), nullable=True),
        sa.Column('status', sa.String(20), nullable=True),
        sa.Column('last_refreshed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symbol', 'report_date', name='uq_earnings_calendar_symbol_report_date'),
    )
    op.create_index('ix_earnings_calendar_symbol', 'earnings_calendar', ['symbol'])
    op.create_index('ix_earnings_calendar_report_date', 'earnings_calendar', ['report_date'])


def downgrade() -> None:
    op.drop_index('ix_earnings_calendar_report_date', table_name='earnings_calendar')
    op.drop_index('ix_earnings_calendar_symbol', table_name='earnings_calendar')
    op.drop_table('earnings_calendar')

    op.drop_index('ix_company_fundamentals_symbol', table_name='company_fundamentals')
    op.drop_table('company_fundamentals')
