"""Celery tasks for market data collection and indicator calculation."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.market_data.binance_client import BinanceWebSocketClient
from app.modules.market_data.service import MarketDataService
from app.modules.system.logging_middleware import TaskLoggingHandler
from app.modules.watchlist.models import Watchlist, WatchlistItem
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Global WebSocket client instance
binance_client: BinanceWebSocketClient = None


async def init_db_session() -> AsyncSession:
    """Create database session."""
    return CeleryAsyncSessionLocal()


async def get_db_session():
    """Get async database session."""
    async with CeleryAsyncSessionLocal() as session:
        yield session


@celery_app.task(name="collect_market_data", bind=True)
def collect_market_data(self):
    """Collect current market data from all watchlist items."""
    try:
        asyncio.run(_collect_market_data())
        return {"status": "success", "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"Error collecting market data: {e}")
        self.retry(exc=e, countdown=10, max_retries=3)


async def _collect_market_data():
    """Async implementation of market data collection."""
    from sqlalchemy import select

    task_name = "collect_market_data"
    start_time = time.time()
    session = await init_db_session()

    try:
        await TaskLoggingHandler.log_task_start(db=session, task_name=task_name)

        # Get all watchlists
        stmt = select(Watchlist)
        result = await session.execute(stmt)
        watchlists = result.scalars().all()

        if not watchlists:
            logger.info("No watchlists found")
            duration_ms = int((time.time() - start_time) * 1000)
            await TaskLoggingHandler.log_task_completion(
                db=session,
                task_name=task_name,
                duration_ms=duration_ms,
                success=True,
                metadata={"watchlists": 0, "symbols": 0},
            )
            await session.commit()
            return

        total_symbols = 0
        for watchlist in watchlists:
            count = await _collect_watchlist_data(session, watchlist)
            total_symbols += count

        duration_ms = int((time.time() - start_time) * 1000)
        await TaskLoggingHandler.log_task_completion(
            db=session,
            task_name=task_name,
            duration_ms=duration_ms,
            success=True,
            metadata={"watchlists": len(watchlists), "symbols": total_symbols},
        )
        await session.commit()

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Error in market data collection: {e}")
        try:
            await session.rollback()
            await TaskLoggingHandler.log_task_completion(
                db=session,
                task_name=task_name,
                duration_ms=duration_ms,
                success=False,
                error_message=str(e),
            )
            await session.commit()
        except Exception:
            pass
        raise
    finally:
        await session.close()


async def _collect_watchlist_data(session: AsyncSession, watchlist: Watchlist) -> int:
    """Collect market data for a specific watchlist. Returns number of symbols processed."""
    from sqlalchemy import select

    count = 0
    try:
        # Get all items in watchlist
        stmt = select(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist.id)
        result = await session.execute(stmt)
        items = result.scalars().all()

        for item in items:
            if item.market_type == "crypto":
                await _collect_crypto_ohlcv(session, watchlist.id, item.symbol)
                count += 1

    except Exception as e:
        logger.error(f"Error collecting watchlist data: {e}")

    return count


async def _collect_crypto_ohlcv(session: AsyncSession, watchlist_id: UUID, symbol: str):
    """Collect OHLCV data for a crypto symbol."""
    client = BinanceWebSocketClient()
    try:
        await client.connect()

        # Get historical klines (1h candles)
        klines = await client.get_historical_klines(symbol, interval="1h", limit=100)

        if not klines:
            logger.warning(f"No klines found for {symbol}")
            return

        # Save each candle
        for kline in klines:
            await MarketDataService.save_ohlcv_candle(
                session, watchlist_id, symbol, "1h", kline
            )

        # Calculate indicators for latest candle
        await MarketDataService.calculate_and_save_indicators(
            session, watchlist_id, symbol, "1h"
        )

        await session.commit()
        logger.info(f"Collected {len(klines)} candles for {symbol}")

        # Log to database
        await TaskLoggingHandler.log_market_data_collected(
            db=session,
            symbol=symbol,
            data_points=len(klines),
            metadata={"interval": "1h", "watchlist_id": str(watchlist_id)},
        )
        await session.commit()

    except Exception as e:
        logger.error(f"Error collecting crypto OHLCV: {e}")
        try:
            await session.rollback()
            await TaskLoggingHandler.log_error(
                db=session,
                error_type="OHLCVCollectionError",
                error_message=str(e),
                category="market_data",
                task_name="collect_market_data",
                metadata={"symbol": symbol},
            )
            await session.commit()
        except Exception:
            pass
    finally:
        await client.disconnect()


@celery_app.task(name="update_indicators", bind=True)
def update_indicators(self):
    """Calculate technical indicators for all watchlist items."""
    try:
        asyncio.run(_update_indicators())
        return {"status": "success", "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"Error updating indicators: {e}")
        self.retry(exc=e, countdown=10, max_retries=3)


async def _update_indicators():
    """Async implementation of indicator updates."""
    from sqlalchemy import select

    task_name = "update_indicators"
    start_time = time.time()
    session = await init_db_session()

    try:
        await TaskLoggingHandler.log_task_start(db=session, task_name=task_name)

        # Get all watchlists
        stmt = select(Watchlist)
        result = await session.execute(stmt)
        watchlists = result.scalars().all()

        total_symbols = 0
        for watchlist in watchlists:
            stmt = select(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist.id)
            result = await session.execute(stmt)
            items = result.scalars().all()

            for item in items:
                await MarketDataService.calculate_and_save_indicators(
                    session, watchlist.id, item.symbol, "1h"
                )
                total_symbols += 1

        await session.commit()

        duration_ms = int((time.time() - start_time) * 1000)
        await TaskLoggingHandler.log_task_completion(
            db=session,
            task_name=task_name,
            duration_ms=duration_ms,
            success=True,
            metadata={"symbols_updated": total_symbols},
        )
        await session.commit()

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Error updating indicators: {e}")
        try:
            await session.rollback()
            await TaskLoggingHandler.log_task_completion(
                db=session,
                task_name=task_name,
                duration_ms=duration_ms,
                success=False,
                error_message=str(e),
            )
            await session.commit()
        except Exception:
            pass
        raise
    finally:
        await session.close()


@celery_app.task(name="cleanup_market_data", bind=True)
def cleanup_market_data(self):
    """Clean up old market data (retention policy)."""
    try:
        asyncio.run(_cleanup_market_data())
        return {"status": "success", "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"Error cleaning up market data: {e}")
        self.retry(exc=e, countdown=60, max_retries=3)


async def _cleanup_market_data():
    """Async implementation of market data cleanup."""
    from sqlalchemy import select

    task_name = "cleanup_market_data"
    start_time = time.time()
    session = await init_db_session()

    try:
        await TaskLoggingHandler.log_task_start(db=session, task_name=task_name)

        # Get all watchlists
        stmt = select(Watchlist)
        result = await session.execute(stmt)
        watchlists = result.scalars().all()

        total_deleted = 0
        for watchlist in watchlists:
            stmt = select(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist.id)
            result = await session.execute(stmt)
            items = result.scalars().all()

            for item in items:
                # Delete candles older than 90 days
                deleted = await MarketDataService.cleanup_old_candles(
                    session, watchlist.id, item.symbol, "1h", days=90
                )
                total_deleted += deleted

        await session.commit()
        logger.info(f"Cleaned up {total_deleted} old candles")

        duration_ms = int((time.time() - start_time) * 1000)
        await TaskLoggingHandler.log_task_completion(
            db=session,
            task_name=task_name,
            duration_ms=duration_ms,
            success=True,
            metadata={"candles_deleted": total_deleted},
        )
        await session.commit()

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Error in market data cleanup: {e}")
        try:
            await session.rollback()
            await TaskLoggingHandler.log_task_completion(
                db=session,
                task_name=task_name,
                duration_ms=duration_ms,
                success=False,
                error_message=str(e),
            )
            await session.commit()
        except Exception:
            pass
        raise
    finally:
        await session.close()


@celery_app.task(name="fetch_binance_ohlcv", bind=True)
def fetch_binance_ohlcv(self, watchlist_id: str, symbol: str):
    """Fetch OHLCV data from Binance for a specific symbol."""
    try:
        asyncio.run(_fetch_binance_ohlcv(UUID(watchlist_id), symbol))
        return {"status": "success", "symbol": symbol}
    except Exception as e:
        logger.error(f"Error fetching Binance OHLCV: {e}")
        self.retry(exc=e, countdown=5, max_retries=5)


async def _fetch_binance_ohlcv(watchlist_id: UUID, symbol: str):
    """Async implementation of Binance OHLCV fetch."""
    task_name = "fetch_binance_ohlcv"
    start_time = time.time()
    session = await init_db_session()
    client = BinanceWebSocketClient()

    try:
        await TaskLoggingHandler.log_task_start(
            db=session,
            task_name=task_name,
            metadata={"symbol": symbol},
        )

        await client.connect()

        total_candles = 0
        # Collect multiple timeframes
        for timeframe, interval in [("1m", "1m"), ("5m", "5m"), ("15m", "15m"), ("1h", "1h")]:
            klines = await client.get_historical_klines(symbol, interval=interval, limit=100)

            if klines:
                for kline in klines:
                    await MarketDataService.save_ohlcv_candle(
                        session, watchlist_id, symbol, timeframe, kline
                    )

                # Calculate indicators
                await MarketDataService.calculate_and_save_indicators(
                    session, watchlist_id, symbol, timeframe
                )
                total_candles += len(klines)

        await session.commit()
        logger.info(f"Fetched OHLCV for {symbol} across multiple timeframes")

        await TaskLoggingHandler.log_market_data_collected(
            db=session,
            symbol=symbol,
            data_points=total_candles,
            metadata={"timeframes": ["1m", "5m", "15m", "1h"], "watchlist_id": str(watchlist_id)},
        )

        duration_ms = int((time.time() - start_time) * 1000)
        await TaskLoggingHandler.log_task_completion(
            db=session,
            task_name=task_name,
            duration_ms=duration_ms,
            success=True,
            metadata={"symbol": symbol, "candles": total_candles},
        )
        await session.commit()
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Error fetching Binance OHLCV: {e}")
        try:
            await session.rollback()
            await TaskLoggingHandler.log_task_completion(
                db=session,
                task_name=task_name,
                duration_ms=duration_ms,
                success=False,
                error_message=str(e),
                metadata={"symbol": symbol},
            )
            await session.commit()
        except Exception:
            pass
        raise
    finally:
        await client.disconnect()
        await session.close()
