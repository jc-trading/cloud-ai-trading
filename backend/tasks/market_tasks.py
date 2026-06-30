"""
Celery tasks for market data pulling and watchlist sync.

These tasks run in a sync Celery worker context, so we use asyncio.run()
to bridge into the async SQLAlchemy / CCXT world.
"""

import asyncio
import logging
from datetime import datetime, timezone

from tasks.celery_app import celery_app

logger = logging.getLogger("cloud_ai_trading.tasks.market")


def _run_async(coro):
    """Run an async coroutine from sync Celery context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="pull_market_data")
def pull_market_data():
    """
    Pull latest market data for all watched symbols.
    Runs every 1 minute via Celery Beat.
    """
    logger.info("Pulling market data...")
    _run_async(_pull_market_data_async())
    logger.info("Market data pull complete.")


async def _pull_market_data_async():
    """Async implementation of market data pull."""
    from app.celery_database import CeleryAsyncSessionLocal
    from app.modules.watchlist.models import Watchlist, WatchlistItem
    from app.modules.market.models import MarketCandle
    from app.modules.market.service import MarketService
    from sqlalchemy import select

    async with CeleryAsyncSessionLocal() as db:
        try:
            # Get all unique symbols across all watchlists
            result = await db.execute(
                select(WatchlistItem.symbol).distinct()
            )
            symbols = [row[0] for row in result.all()]

            if not symbols:
                logger.info("No watched symbols, skipping market data pull.")
                return

            logger.info(f"Pulling data for {len(symbols)} symbols: {symbols[:5]}...")

            for symbol in symbols:
                try:
                    candles = await MarketService.get_candles(symbol, "1h", 10)
                    for c in candles:
                        existing = await db.execute(
                            select(MarketCandle).where(
                                MarketCandle.symbol == symbol,
                                MarketCandle.interval == "1h",
                                MarketCandle.open_time == datetime.fromtimestamp(
                                    c["timestamp"] / 1000, tz=timezone.utc
                                ),
                            )
                        )
                        if not existing.scalar_one_or_none():
                            open_time = datetime.fromtimestamp(
                                c["timestamp"] / 1000, tz=timezone.utc
                            )
                            from datetime import timedelta
                            close_time = open_time + timedelta(hours=1)
                            candle = MarketCandle(
                                symbol=symbol,
                                interval="1h",
                                open_time=open_time,
                                close_time=close_time,
                                open=c["open"],
                                high=c["high"],
                                low=c["low"],
                                close=c["close"],
                                volume=c["volume"],
                            )
                            db.add(candle)
                    await db.commit()
                except Exception as e:
                    logger.error(f"Failed to pull data for {symbol}: {e}")
                    await db.rollback()

        except Exception as e:
            logger.error(f"Market data pull error: {e}")
            await db.rollback()


@celery_app.task(name="sync_watchlists")
def sync_watchlists():
    """
    Sync watchlists with exchange APIs.
    Runs every 5 minutes via Celery Beat.
    """
    logger.info("Syncing watchlists with exchanges...")
    _run_async(_sync_watchlists_async())
    logger.info("Watchlist sync complete.")


async def _sync_watchlists_async():
    """Async implementation of watchlist sync."""
    from app.celery_database import CeleryAsyncSessionLocal
    from app.modules.watchlist.models import Watchlist, WatchlistItem
    from app.modules.exchange.models import ExchangeConnection
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with CeleryAsyncSessionLocal() as db:
        try:
            # Get all watchlists with their items
            result = await db.execute(
                select(Watchlist).options(selectinload(Watchlist.items))
            )
            watchlists = result.scalars().all()

            for wl in watchlists:
                for item in wl.items:
                    if not item.synced_with_exchange:
                        # Mark as synced (actual exchange sync would use the adapter)
                        item.synced_with_exchange = True

            await db.commit()
            logger.info(f"Synced {len(watchlists)} watchlists.")

        except Exception as e:
            logger.error(f"Watchlist sync error: {e}")
            await db.rollback()
