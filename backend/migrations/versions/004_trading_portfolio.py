"""Add trading signals and portfolio tracking tables

Revision ID: 004
Revises: 003_ohlcv_tables
Create Date: 2026-04-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create trading signals table
    op.create_table(
        'trading_signals',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('watchlist_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('signal_type', sa.String(20), nullable=False),  # BUY, SELL, HOLD, STRONG_BUY, STRONG_SELL
        sa.Column('signal_strength', sa.Numeric(precision=5, scale=2), nullable=False),  # 0-100
        sa.Column('confidence', sa.Numeric(precision=5, scale=2), nullable=False),  # 0-100
        sa.Column('indicators_used', sa.JSON(), nullable=True),  # {"EMA_12": true, "RSI": true, ...}
        sa.Column('recommendation', sa.Text(), nullable=True),
        sa.Column('strategy', sa.String(50), nullable=True),  # MOMENTUM, TREND_FOLLOWING, CONTRARIAN, COMPOSITE
        sa.Column('signal_timestamp', sa.DateTime(timezone=True), nullable=False),  # Candle timestamp
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('watchlist_id', 'symbol', 'signal_timestamp', 'strategy', name='uq_signal_unique')
    )
    op.create_index('ix_trading_signals_watchlist_id', 'trading_signals', ['watchlist_id'])
    op.create_index('ix_trading_signals_symbol', 'trading_signals', ['symbol'])
    op.create_index('ix_trading_signals_signal_type', 'trading_signals', ['signal_type'])
    op.create_index('ix_trading_signals_timestamp', 'trading_signals', ['signal_timestamp'])

    # Create alert rules table
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('watchlist_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('rule_type', sa.String(50), nullable=False),  # PRICE_ABOVE, PRICE_BELOW, RSI_OVERBOUGHT, etc
        sa.Column('rule_config', sa.JSON(), nullable=False),  # Flexible rule configuration
        sa.Column('alert_channels', sa.JSON(), nullable=False),  # {"email": true, "telegram": true, "webhook": "..."}
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_alert_rules_watchlist_id', 'alert_rules', ['watchlist_id'])
    op.create_index('ix_alert_rules_symbol', 'alert_rules', ['symbol'])
    op.create_index('ix_alert_rules_enabled', 'alert_rules', ['enabled'])

    # Create alerts history table
    op.create_table(
        'alerts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('alert_rule_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('alert_type', sa.String(50), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='sent'),  # sent, confirmed, dismissed
        sa.Column('alert_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['alert_rule_id'], ['alert_rules.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_alerts_alert_rule_id', 'alerts', ['alert_rule_id'])
    op.create_index('ix_alerts_symbol', 'alerts', ['symbol'])
    op.create_index('ix_alerts_status', 'alerts', ['status'])
    op.create_index('ix_alerts_timestamp', 'alerts', ['alert_timestamp'])

    # Create positions table
    op.create_table(
        'positions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('watchlist_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('entry_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column('entry_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('exit_price', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('exit_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),  # open, closed, partial
        sa.Column('position_type', sa.String(10), nullable=False, server_default='LONG'),  # LONG, SHORT
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_positions_watchlist_id', 'positions', ['watchlist_id'])
    op.create_index('ix_positions_symbol', 'positions', ['symbol'])
    op.create_index('ix_positions_status', 'positions', ['status'])
    op.create_index('ix_positions_entry_date', 'positions', ['entry_date'])

    # Create portfolio stats table
    op.create_table(
        'portfolio_stats',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('watchlist_id', sa.UUID(), nullable=False),
        sa.Column('total_invested', sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column('current_value', sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column('unrealized_pnl', sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column('realized_pnl', sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column('total_return_percent', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('win_rate', sa.Numeric(precision=5, scale=2), nullable=True),  # Percentage
        sa.Column('max_drawdown', sa.Numeric(precision=5, scale=2), nullable=True),  # Percentage
        sa.Column('total_trades', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('winning_trades', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('losing_trades', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_updated', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('watchlist_id', name='uq_portfolio_stats_per_watchlist')
    )
    op.create_index('ix_portfolio_stats_watchlist_id', 'portfolio_stats', ['watchlist_id'])


def downgrade() -> None:
    op.drop_index('ix_portfolio_stats_watchlist_id', table_name='portfolio_stats')
    op.drop_table('portfolio_stats')

    op.drop_index('ix_positions_entry_date', table_name='positions')
    op.drop_index('ix_positions_status', table_name='positions')
    op.drop_index('ix_positions_symbol', table_name='positions')
    op.drop_index('ix_positions_watchlist_id', table_name='positions')
    op.drop_table('positions')

    op.drop_index('ix_alerts_timestamp', table_name='alerts')
    op.drop_index('ix_alerts_status', table_name='alerts')
    op.drop_index('ix_alerts_symbol', table_name='alerts')
    op.drop_index('ix_alerts_alert_rule_id', table_name='alerts')
    op.drop_table('alerts')

    op.drop_index('ix_alert_rules_enabled', table_name='alert_rules')
    op.drop_index('ix_alert_rules_symbol', table_name='alert_rules')
    op.drop_index('ix_alert_rules_watchlist_id', table_name='alert_rules')
    op.drop_table('alert_rules')

    op.drop_index('ix_trading_signals_timestamp', table_name='trading_signals')
    op.drop_index('ix_trading_signals_signal_type', table_name='trading_signals')
    op.drop_index('ix_trading_signals_symbol', table_name='trading_signals')
    op.drop_index('ix_trading_signals_watchlist_id', table_name='trading_signals')
    op.drop_table('trading_signals')
