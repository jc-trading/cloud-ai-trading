"""
Celery tasks for AI analysis using Claude API.

Runs scheduled analysis for all users with active strategies/watchlists.
"""

import asyncio
import logging

from tasks.celery_app import celery_app
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger("cloud_ai_trading.tasks.analysis")


def _run_async(coro):
    """Run an async coroutine from sync Celery context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="run_scheduled_analysis")
def run_scheduled_analysis():
    """
    Run AI analysis on all active user watchlists.
    Runs every 3 minutes via Celery Beat.
    """
    logger.info("Running scheduled AI analysis...")
    _run_async(_run_scheduled_analysis_async())
    logger.info("Scheduled AI analysis complete.")


async def _run_scheduled_analysis_async():
    """
    Async implementation:
    1. Get all users with active strategies
    2. For each user's watched symbols:
       a. Calculate technical indicators
       b. Call Claude API for analysis
       c. Store result
       d. If signal is strong enough, create a TradeSignal
    """
    from app.celery_database import CeleryAsyncSessionLocal
    from app.modules.auth.models import User
    from app.modules.strategy.models import QuantStrategy
    from app.modules.watchlist.service import WatchlistService
    from app.modules.analysis.service import AnalysisService
    from app.modules.analysis.models import AnalysisType
    from app.modules.trading.service import TradingService
    from sqlalchemy import select

    async with CeleryAsyncSessionLocal() as db:
        try:
            # Get users with active strategies
            result = await db.execute(
                select(QuantStrategy.user_id)
                .where(QuantStrategy.is_active == True)
                .distinct()
            )
            user_ids = [row[0] for row in result.all()]

            if not user_ids:
                logger.info("No users with active strategies, skipping.")
                return

            for user_id in user_ids:
                try:
                    # Get user's watched symbols
                    symbols = await WatchlistService.get_all_watched_symbols(db, user_id)
                    if not symbols:
                        continue

                    logger.info(f"Analyzing {len(symbols)} symbols for user {user_id}")

                    for symbol in symbols:
                        try:
                            analysis = await AnalysisService.run_analysis(
                                db,
                                user_id,
                                symbol,
                                analysis_type=AnalysisType.SCHEDULED,
                            )

                            # Create signal if confidence is high enough (>= 60)
                            if analysis.confidence >= 60 and analysis.action.value != "hold":
                                await TradingService.create_signal(
                                    db,
                                    user_id,
                                    symbol,
                                    signal_type="ai",
                                    action=analysis.action.value,
                                    confidence=analysis.confidence,
                                    meta_data={
                                        "analysis_id": str(analysis.id),
                                        "entry_price": float(analysis.entry_price) if analysis.entry_price else None,
                                        "stop_loss": float(analysis.stop_loss) if analysis.stop_loss else None,
                                        "take_profit": float(analysis.take_profit) if analysis.take_profit else None,
                                    },
                                )
                                logger.info(
                                    f"Signal created: {analysis.action.value} {symbol} "
                                    f"(confidence: {analysis.confidence}%)"
                                )

                            await db.commit()
                        except SoftTimeLimitExceeded:  # soft time limit must wind the task down
                            raise
                        except Exception as e:
                            logger.error(f"Analysis failed for {symbol}: {e}")
                            await db.rollback()

                except SoftTimeLimitExceeded:  # soft time limit must wind the task down
                    raise
                except Exception as e:
                    logger.error(f"Analysis failed for user {user_id}: {e}")

        except SoftTimeLimitExceeded:  # soft time limit must wind the task down
            raise
        except Exception as e:
            logger.error(f"Scheduled analysis error: {e}")


@celery_app.task(name="tasks.analysis_tasks.run_manual_analysis")
def run_manual_analysis(user_id: str, symbol: str):
    """
    Run AI analysis for a specific symbol triggered manually by user.
    """
    logger.info(f"Running manual analysis for user={user_id}, symbol={symbol}")
    _run_async(_run_manual_analysis_async(user_id, symbol))
    logger.info(f"Manual analysis complete for {symbol}")


async def _run_manual_analysis_async(user_id: str, symbol: str):
    """Async implementation of manual analysis."""
    from uuid import UUID
    from app.celery_database import CeleryAsyncSessionLocal
    from app.modules.analysis.service import AnalysisService
    from app.modules.analysis.models import AnalysisType

    async with CeleryAsyncSessionLocal() as db:
        try:
            await AnalysisService.run_analysis(
                db,
                UUID(user_id),
                symbol,
                analysis_type=AnalysisType.MANUAL,
            )
            await db.commit()
        except SoftTimeLimitExceeded:  # soft time limit must wind the task down
            raise
        except Exception as e:
            logger.error(f"Manual analysis error for {symbol}: {e}")
            await db.rollback()
