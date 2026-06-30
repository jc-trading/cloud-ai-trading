"""Initial database tables + super admin seed

Revision ID: 001_initial
Revises: None
Create Date: 2026-04-08
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === USERS ===
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("timezone", sa.String(50), server_default="UTC"),
        sa.Column("country", sa.String(100), server_default=""),
        sa.Column("language", sa.String(10), server_default="en"),
        sa.Column("currency", sa.String(10), server_default="USD"),
        sa.Column("role", sa.String(20), server_default="basic", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # === EXCHANGE CONNECTIONS ===
    op.create_table(
        "exchange_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exchange_type", sa.String(20), nullable=False),
        sa.Column("api_key_encrypted", sa.String(512), nullable=False),
        sa.Column("api_secret_encrypted", sa.String(512), nullable=False),
        sa.Column("passphrase_encrypted", sa.String(512), nullable=True),
        sa.Column("permissions", postgresql.JSONB(), server_default=sa.text("'[\"read\"]'::jsonb")),
        sa.Column("trading_mode", sa.String(20), server_default="simulate"),
        sa.Column("ip_whitelist", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_exchange_connections_user_id", "exchange_connections", ["user_id"])

    # === WATCHLISTS ===
    op.create_table(
        "watchlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), server_default="My Watchlist"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_watchlists_user_id", "watchlists", ["user_id"])

    # === WATCHLIST ITEMS ===
    op.create_table(
        "watchlist_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("watchlist_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("exchange_type", sa.String(20), server_default="binance"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("synced_with_exchange", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_watchlist_items_watchlist_id", "watchlist_items", ["watchlist_id"])

    # === MARKET CANDLES ===
    op.create_table(
        "market_candles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("exchange_type", sa.String(20), server_default="binance"),
        sa.Column("interval", sa.String(10), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(20, 8), nullable=False),
        sa.Column("high", sa.Numeric(20, 8), nullable=False),
        sa.Column("low", sa.Numeric(20, 8), nullable=False),
        sa.Column("close", sa.Numeric(20, 8), nullable=False),
        sa.Column("volume", sa.Numeric(30, 8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_candles_symbol", "market_candles", ["symbol"])
    op.create_index(
        "ix_candles_symbol_interval_time",
        "market_candles",
        ["symbol", "interval", "open_time"],
        unique=True,
    )

    # === AI ANALYSIS RESULTS ===
    op.create_table(
        "ai_analysis_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("exchange_type", sa.String(20), server_default="binance"),
        sa.Column("analysis_type", sa.String(20), nullable=False),
        sa.Column("indicators_snapshot", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("claude_response", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Integer(), server_default=sa.text("0")),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_loss", sa.Numeric(20, 8), nullable=True),
        sa.Column("take_profit", sa.Numeric(20, 8), nullable=True),
        sa.Column("risk_reward_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("prompt_used", sa.Text(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), server_default=sa.text("0")),
        sa.Column("api_cost", sa.Numeric(10, 6), server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_ai_analysis_user_id", "ai_analysis_results", ["user_id"])
    op.create_index("ix_ai_analysis_symbol", "ai_analysis_results", ["symbol"])

    # === QUANT STRATEGIES ===
    op.create_table(
        "quant_strategies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("symbols", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb")),
        sa.Column("timeframe", sa.String(10), server_default="1h"),
        sa.Column("indicators_config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("entry_conditions", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("exit_conditions", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("position_sizing", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("stop_loss_pct", sa.Numeric(5, 2), server_default=sa.text("3.0")),
        sa.Column("take_profit_pct", sa.Numeric(5, 2), server_default=sa.text("8.0")),
        sa.Column("max_positions", sa.Integer(), server_default=sa.text("5")),
        sa.Column("cooldown_hours", sa.Integer(), server_default=sa.text("24")),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("backtest_results", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_quant_strategies_user_id", "quant_strategies", ["user_id"])

    # === TRADES ===
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exchange_connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exchange_connections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trading_mode", sa.String(20), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=True),
        sa.Column("filled_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("exchange_order_id", sa.String(100), nullable=True),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ai_signal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pnl", sa.Numeric(20, 8), nullable=True),
        sa.Column("pnl_percentage", sa.Numeric(10, 4), nullable=True),
        sa.Column("fee", sa.Numeric(20, 8), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_trades_user_id", "trades", ["user_id"])
    op.create_index("ix_trades_symbol", "trades", ["symbol"])

    # === SIMULATE PORTFOLIOS ===
    op.create_table(
        "simulate_portfolios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), unique=True),
        sa.Column("initial_balance", sa.Numeric(20, 8), server_default=sa.text("10000.0")),
        sa.Column("current_balance", sa.Numeric(20, 8), server_default=sa.text("10000.0")),
        sa.Column("total_pnl", sa.Numeric(20, 8), server_default=sa.text("0")),
        sa.Column("total_trades", sa.Integer(), server_default=sa.text("0")),
        sa.Column("win_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("loss_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # === TRADE SIGNALS ===
    op.create_table(
        "trade_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("signal_type", sa.String(20), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Integer(), server_default=sa.text("0")),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("is_executed", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_trade_signals_user_id", "trade_signals", ["user_id"])

    # === ACTIVITY LOGS ===
    op.create_table(
        "activity_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # =============================================
    # SEED: Super Admin Account
    # =============================================
    # Password "Abc1234#" hashed with bcrypt (direct bcrypt library)
    op.execute(
        """
        INSERT INTO users (id, name, email, hashed_password, role, is_active, is_verified, timezone, country, language, currency, email_verified_at)
        VALUES (
            gen_random_uuid(),
            'Super Admin',
            'jiacong9@gmail.com',
            '$2b$12$jcUtm9ftB/DGXoDcnVQd/uJIE6P7y5SA.1yC7/HDJZSRlCZGznTOu',
            'super_admin',
            true,
            true,
            'Asia/Kuala_Lumpur',
            'Malaysia',
            'en',
            'USD',
            now()
        )
        ON CONFLICT (email) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.drop_table("activity_logs")
    op.drop_table("trade_signals")
    op.drop_table("simulate_portfolios")
    op.drop_table("trades")
    op.drop_table("quant_strategies")
    op.drop_table("ai_analysis_results")
    op.drop_table("market_candles")
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
    op.drop_table("exchange_connections")
    op.drop_table("users")
