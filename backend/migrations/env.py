"""
Alembic environment configuration.
"""

from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context

# Import all models so Alembic can detect them
from app.database import Base
from app.modules.auth.models import User
from app.modules.exchange.models import ExchangeConnection
from app.modules.watchlist.models import Watchlist, WatchlistItem
from app.modules.market_data.models import OHLCVCandle, TechnicalIndicator, MarketDataEvent
from app.modules.trading.models import TradingSignal, AlertRule, Alert, Position, PortfolioStats
from app.modules.system.models import SystemLog, SystemMetric, TaskStatus

import os

config = context.config

# Override sqlalchemy.url from environment variable
db_url = os.environ.get("DATABASE_URL_SYNC")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Get the database URL from environment
    db_url = os.environ.get("DATABASE_URL_SYNC")
    if not db_url:
        db_url = config.get_main_option("sqlalchemy.url")

    # Ensure URL uses the correct driver
    # Replace postgresql:// with postgresql+psycopg:// for sync access
    if db_url and db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    # Create engine with explicit pool
    connectable = create_engine(
        db_url,
        poolclass=pool.NullPool,
        echo=False,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
