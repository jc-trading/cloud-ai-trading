"""Create OHLCV and market data tables

Revision ID: 003
Revises: 002_watchlist_market_type
Create Date: 2026-04-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002_watchlist_market_type'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create OHLCV candles table
    op.create_table(
        'ohlcv_candles',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('watchlist_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('timeframe', sa.String(10), nullable=False),  # 1m, 5m, 15m, 1h, 4h, 1d
        sa.Column('open_time', sa.DateTime(timezone=True), nullable=False),  # Candle open time (UTC)
        sa.Column('close_time', sa.DateTime(timezone=True), nullable=False),  # Candle close time (UTC)
        sa.Column('open_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('high_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('low_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('close_price', sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column('volume', sa.Numeric(precision=20, scale=8), nullable=False),  # Base asset volume
        sa.Column('quote_volume', sa.Numeric(precision=20, scale=8), nullable=False),  # Quote asset volume (USDT)
        sa.Column('trades_count', sa.Integer(), nullable=True),  # Number of trades in candle
        sa.Column('taker_buy_base_volume', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('taker_buy_quote_volume', sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('watchlist_id', 'symbol', 'timeframe', 'open_time', name='uq_ohlcv_unique_candle')
    )
    op.create_index('ix_ohlcv_candles_watchlist_id', 'ohlcv_candles', ['watchlist_id'])
    op.create_index('ix_ohlcv_candles_symbol', 'ohlcv_candles', ['symbol'])
    op.create_index('ix_ohlcv_candles_timeframe', 'ohlcv_candles', ['timeframe'])
    op.create_index('ix_ohlcv_candles_open_time', 'ohlcv_candles', ['open_time'])

    # Create technical indicators table
    op.create_table(
        'technical_indicators',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('ohlcv_candle_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('timeframe', sa.String(10), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        # Trend Indicators
        sa.Column('ema_12', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('ema_26', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('ema_50', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('ema_200', sa.Numeric(precision=18, scale=8), nullable=True),
        # Momentum Indicators
        sa.Column('rsi_14', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('macd', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('macd_signal', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('macd_histogram', sa.Numeric(precision=18, scale=8), nullable=True),
        # Volatility Indicators
        sa.Column('atr_14', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('bb_upper', sa.Numeric(precision=18, scale=8), nullable=True),  # Bollinger Bands upper
        sa.Column('bb_middle', sa.Numeric(precision=18, scale=8), nullable=True),  # SMA 20
        sa.Column('bb_lower', sa.Numeric(precision=18, scale=8), nullable=True),  # Bollinger Bands lower
        sa.Column('bb_width', sa.Numeric(precision=18, scale=8), nullable=True),  # BB width
        sa.Column('bb_position', sa.Numeric(precision=5, scale=2), nullable=True),  # % position in bands
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['ohlcv_candle_id'], ['ohlcv_candles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ohlcv_candle_id', name='uq_indicators_per_candle')
    )
    op.create_index('ix_technical_indicators_symbol_timeframe', 'technical_indicators', ['symbol', 'timeframe'])
    op.create_index('ix_technical_indicators_timestamp', 'technical_indicators', ['timestamp'])

    # Create market data events table (for tracking price updates, alert triggers, etc)
    op.create_table(
        'market_data_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('watchlist_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),  # price_update, indicator_alert, etc
        sa.Column('event_data', sa.JSON(), nullable=True),  # flexible data storage
        sa.Column('price', sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_market_data_events_watchlist_id', 'market_data_events', ['watchlist_id'])
    op.create_index('ix_market_data_events_symbol', 'market_data_events', ['symbol'])
    op.create_index('ix_market_data_events_timestamp', 'market_data_events', ['timestamp'])


def downgrade() -> None:
    op.drop_index('ix_market_data_events_timestamp', table_name='market_data_events')
    op.drop_index('ix_market_data_events_symbol', table_name='market_data_events')
    op.drop_index('ix_market_data_events_watchlist_id', table_name='market_data_events')
    op.drop_table('market_data_events')

    op.drop_index('ix_technical_indicators_timestamp', table_name='technical_indicators')
    op.drop_index('ix_technical_indicators_symbol_timeframe', table_name='technical_indicators')
    op.drop_table('technical_indicators')

    op.drop_index('ix_ohlcv_candles_open_time', table_name='ohlcv_candles')
    op.drop_index('ix_ohlcv_candles_timeframe', table_name='ohlcv_candles')
    op.drop_index('ix_ohlcv_candles_symbol', table_name='ohlcv_candles')
    op.drop_index('ix_ohlcv_candles_watchlist_id', table_name='ohlcv_candles')
    op.drop_table('ohlcv_candles')
