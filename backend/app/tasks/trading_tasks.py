"""Trading-related Celery tasks."""

import logging
import time
from decimal import Decimal
from sqlalchemy import select

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.market_data.models import OHLCVCandle, TechnicalIndicator
from app.modules.trading.signals import TradingSignalGenerator
from app.modules.trading.portfolio import PortfolioManager
from app.modules.notifications.telegram import TelegramNotifier
from app.modules.watchlist.models import Watchlist
from app.modules.analysis.multi_ai_provider import analyze_with_ai
from app.modules.system.logging_middleware import TaskLoggingHandler
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def get_async_session():
    """Create async database session."""
    return CeleryAsyncSessionLocal()


@celery_app.task(name="generate_trading_signals", bind=True)
def generate_trading_signals(self):
    """
    Generate trading signals for all active watchlists.
    Runs every minute.
    """
    import asyncio

    try:
        asyncio.run(_generate_trading_signals_async())
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error generating trading signals: {e}")
        raise


async def _generate_trading_signals_async():
    """Async implementation of trading signal generation."""
    task_name = "generate_trading_signals"
    start_time = time.time()
    session = await get_async_session()

    try:
        await TaskLoggingHandler.log_task_start(db=session, task_name=task_name)

        # Get all watchlists with items. Watchlist.is_active is a Python property,
        # not a SQL expression, so filtering happens after loading.
        stmt = select(Watchlist)
        result = await session.execute(stmt)
        watchlists = [
            watchlist for watchlist in result.scalars().all() if watchlist.symbols
        ]

        logger.info(f"Processing {len(watchlists)} active watchlists")

        total_symbols = sum(len(w.symbols) for w in watchlists)
        for watchlist in watchlists:
            # Get latest candle for each symbol in watchlist
            for symbol in watchlist.symbols:
                await _generate_signal_for_symbol(session, watchlist, symbol)

        await session.commit()

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
        logger.error(f"Error in _generate_trading_signals_async: {e}")
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


async def _generate_signal_for_symbol(session, watchlist, symbol):
    """Generate signal for a specific symbol."""
    try:
        # Get latest candle
        stmt = (
            select(OHLCVCandle)
            .where(
                OHLCVCandle.watchlist_id == watchlist.id,
                OHLCVCandle.symbol == symbol,
            )
            .order_by(OHLCVCandle.close_time.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        latest_candle = result.scalar_one_or_none()

        if not latest_candle:
            logger.warning(f"No candle data for {symbol}")
            return

        # Get latest technical indicators
        stmt = (
            select(TechnicalIndicator)
            .where(
                TechnicalIndicator.watchlist_id == watchlist.id,
                TechnicalIndicator.symbol == symbol,
            )
            .order_by(TechnicalIndicator.timestamp.desc())
            .limit(2)
        )
        result = await session.execute(stmt)
        indicators = result.scalars().all()

        if not indicators:
            logger.warning(f"No indicator data for {symbol}")
            return

        current_indicator = indicators[0]
        prev_indicator = indicators[1] if len(indicators) > 1 else None
        required_values = {
            "ema_12": current_indicator.ema_12,
            "ema_26": current_indicator.ema_26,
            "rsi_14": current_indicator.rsi_14,
            "bb_upper": current_indicator.bb_upper,
            "bb_middle": current_indicator.bb_middle,
            "bb_lower": current_indicator.bb_lower,
        }
        missing_values = [
            name for name, value in required_values.items() if value is None
        ]
        if missing_values:
            logger.warning(
                f"Incomplete indicator data for {symbol}; missing {missing_values}"
            )
            return

        # Generate momentum signal
        momentum_signal = await TradingSignalGenerator.generate_momentum_signal(
            session=session,
            watchlist_id=watchlist.id,
            symbol=symbol,
            ema_12=Decimal(str(current_indicator.ema_12)),
            ema_26=Decimal(str(current_indicator.ema_26)),
            prev_ema_12=(
                Decimal(str(prev_indicator.ema_12))
                if prev_indicator and prev_indicator.ema_12 is not None
                else None
            ),
            prev_ema_26=(
                Decimal(str(prev_indicator.ema_26))
                if prev_indicator and prev_indicator.ema_26 is not None
                else None
            ),
            signal_timestamp=latest_candle.close_time,
        )

        # Generate contrarian signal
        contrarian_signal = (
            await TradingSignalGenerator.generate_contrarian_signal(
                rsi=Decimal(str(current_indicator.rsi_14)),
                bb_upper=Decimal(str(current_indicator.bb_upper)),
                bb_lower=Decimal(str(current_indicator.bb_lower)),
                current_price=Decimal(str(latest_candle.close_price)),
            )
        )

        # ════════════════════════════════════════════════════════════════════════════════
        # P1 NEW: Generate MACD and Bollinger Band signals
        # ════════════════════════════════════════════════════════════════════════════════
        macd_signal = (
            await TradingSignalGenerator.generate_macd_signal(
                macd=(
                    Decimal(str(current_indicator.macd))
                    if current_indicator.macd is not None
                    else Decimal(0)
                ),
                macd_signal=(
                    Decimal(str(current_indicator.macd_signal))
                    if current_indicator.macd_signal is not None
                    else Decimal(0)
                ),
                prev_macd=(
                    Decimal(str(prev_indicator.macd))
                    if prev_indicator and prev_indicator.macd is not None
                    else None
                ),
                prev_macd_signal=(
                    Decimal(str(prev_indicator.macd_signal))
                    if prev_indicator and prev_indicator.macd_signal is not None
                    else None
                ),
            )
        )

        bb_signal = (
            await TradingSignalGenerator.generate_bb_breakout_signal(
                current_price=Decimal(str(latest_candle.close_price)),
                bb_upper=Decimal(str(current_indicator.bb_upper)),
                bb_middle=Decimal(str(current_indicator.bb_middle)),
                bb_lower=Decimal(str(current_indicator.bb_lower)),
            )
        )

        # Save all 4 signals to database
        momentum_db = await TradingSignalGenerator.save_signal(
            session=session,
            watchlist_id=watchlist.id,
            symbol=symbol,
            signal_data=momentum_signal,
            signal_timestamp=latest_candle.close_time,
        )

        contrarian_db = await TradingSignalGenerator.save_signal(
            session=session,
            watchlist_id=watchlist.id,
            symbol=symbol,
            signal_data=contrarian_signal,
            signal_timestamp=latest_candle.close_time,
        )

        macd_db = await TradingSignalGenerator.save_signal(
            session=session,
            watchlist_id=watchlist.id,
            symbol=symbol,
            signal_data=macd_signal,
            signal_timestamp=latest_candle.close_time,
        )

        bb_db = await TradingSignalGenerator.save_signal(
            session=session,
            watchlist_id=watchlist.id,
            symbol=symbol,
            signal_data=bb_signal,
            signal_timestamp=latest_candle.close_time,
        )

        # ════════════════════════════════════════════════════════════════════════════════
        # P0 + P1 ENHANCEMENT: Call AI to analyze all 4 signals (with signal filtering)
        # ════════════════════════════════════════════════════════════════════════════════
        claude_result = None  # Initialize before try block to ensure visibility in metadata
        all_signals = [momentum_signal, contrarian_signal, macd_signal, bb_signal]

        # Find the strongest signal by distance from neutral 50.
        strongest_signal_data = max(
            zip([momentum_db, contrarian_db, macd_db, bb_db], all_signals),
            key=lambda x: _signal_strength_distance(x[1]),
        )[0]
        strongest_signal = strongest_signal_data

        # ⚠️ COST OPTIMIZATION: Check if signals warrant AI analysis
        should_analyze, filter_reason = _should_call_ai(all_signals)

        try:
            if not should_analyze:
                logger.info(f"Skipping AI analysis for {symbol}: {filter_reason}")
            else:
                # Build indicators dict for AI
                indicators_dict = {
                    "rsi": float(current_indicator.rsi_14),
                    "ema_12": float(current_indicator.ema_12),
                    "ema_26": float(current_indicator.ema_26),
                    "bb_upper": float(current_indicator.bb_upper),
                    "bb_middle": float(current_indicator.bb_middle),
                    "bb_lower": float(current_indicator.bb_lower),
                    "macd_line": (
                        float(current_indicator.macd)
                        if current_indicator.macd is not None
                        else None
                    ),
                    "macd_signal": (
                        float(current_indicator.macd_signal)
                        if current_indicator.macd_signal is not None
                        else None
                    ),
                    "macd_histogram": (
                        float(current_indicator.macd_histogram)
                        if current_indicator.macd_histogram is not None
                        else None
                    ),
                    "atr": (
                        float(current_indicator.atr_14)
                        if current_indicator.atr_14 is not None
                        else None
                    ),
                    "current_price": float(latest_candle.close_price),
                    "volume": float(latest_candle.volume),
                    "change_24h": 0.0,
                    # P1: Add all 4 signals for AI to analyze
                    "all_signals": {
                        "momentum": {
                            "type": momentum_signal["signal_type"],
                            "strength": float(momentum_signal["signal_strength"]),
                            "confidence": float(momentum_signal["confidence"]),
                        },
                        "contrarian": {
                            "type": contrarian_signal["signal_type"],
                            "strength": float(contrarian_signal["signal_strength"]),
                            "confidence": float(contrarian_signal["confidence"]),
                        },
                        "macd": {
                            "type": macd_signal["signal_type"],
                            "strength": float(macd_signal["signal_strength"]),
                            "confidence": float(macd_signal["confidence"]),
                        },
                        "bollinger_band": {
                            "type": bb_signal["signal_type"],
                            "strength": float(bb_signal["signal_strength"]),
                            "confidence": float(bb_signal["confidence"]),
                        },
                    },
                }

                # Call AI for enhanced analysis (supports Claude, OpenAI, DeepSeek)
                claude_result = await analyze_with_ai(
                    symbol=symbol,
                    indicators=indicators_dict,
                )

            # Merge AI results into the strongest signal (if analysis was done)
            if claude_result:
                # Update signal with Claude's confidence
                strongest_signal.confidence = Decimal(
                    str(claude_result.get("confidence", strongest_signal.confidence))
                )

                # Store Claude analysis in indicators_used JSON
                indicators_used = dict(strongest_signal.indicators_used or {})
                indicators_used["claude_analysis"] = {
                    "action": claude_result.get("action"),
                    "confidence": claude_result.get("confidence"),
                    "reason": claude_result.get("reason"),
                    "entry_price": claude_result.get("entry_price"),
                    "stop_loss": claude_result.get("stop_loss"),
                    "take_profit": claude_result.get("take_profit"),
                    "risk_reward_ratio": claude_result.get("risk_reward_ratio"),
                    "key_factors": claude_result.get("key_factors"),
                    "risk_warning": claude_result.get("risk_warning"),
                    "tokens_used": claude_result.get("tokens_used", 0),
                    "api_cost": claude_result.get("api_cost", 0),
                    "all_signals": indicators_dict["all_signals"],
                }
                strongest_signal.indicators_used = indicators_used

                # Use AI's reason as recommendation if available
                if claude_result.get("reason"):
                    strongest_signal.recommendation = claude_result.get("reason")

                provider = claude_result.get('provider', 'unknown')
                logger.info(
                    f"AI analysis ({provider}) for {symbol}: action={claude_result.get('action')}, "
                    f"confidence={claude_result.get('confidence')}, "
                    f"tokens={claude_result.get('tokens_used', 0)}, "
                    f"cost=${claude_result.get('api_cost', 0):.6f}"
                )
        except Exception as ai_error:
            # Log AI error but don't fail the signal generation
            logger.warning(
                f"AI analysis failed for {symbol}: {ai_error}. "
                "Continuing with rule-based signal."
            )
            # Keep the rule-based signal as-is

        # ════════════════════════════════════════════════════════════════════════════════

        # Log trading signal to database (all 4 strategies)
        await TaskLoggingHandler.log_trading_signal(
            db=session,
            symbol=symbol,
            signal_type=momentum_signal["signal_type"],
            metadata={
                "momentum": momentum_signal["signal_type"],
                "contrarian": contrarian_signal["signal_type"],
                "macd": macd_signal["signal_type"],
                "bollinger_band": bb_signal["signal_type"],
                "claude_action": claude_result.get("action") if claude_result else None,
                "claude_confidence": claude_result.get("confidence") if claude_result else None,
                "price": float(latest_candle.close_price),
            },
        )

        # Send Telegram notification for STRONG signals
        if momentum_signal["signal_type"] in ["STRONG_BUY", "STRONG_SELL"]:
            notifier = TelegramNotifier()
            await notifier.send_trading_signal(
                symbol=symbol,
                signal_type=momentum_signal["signal_type"],
                signal_strength=float(momentum_signal["signal_strength"]),
                confidence=float(strongest_signal.confidence),  # Use updated confidence
                recommendation=(
                    strongest_signal.recommendation
                    or momentum_signal["recommendation"]
                ),
            )

        logger.info(
            f"Signal generated for {symbol}: "
            f"momentum={momentum_signal['signal_type']}, "
            f"contrarian={contrarian_signal['signal_type']}"
        )

    except Exception as e:
        logger.error(f"Error generating signal for {symbol}: {e}")
        try:
            await TaskLoggingHandler.log_error(
                db=session,
                error_type="SignalGenerationError",
                error_message=str(e),
                category="trading",
                task_name="generate_trading_signals",
                metadata={"symbol": symbol},
            )
            await session.commit()
        except Exception:
            pass


def _signal_strength_distance(signal_data: dict) -> Decimal:
    """Measure conviction as distance from neutral 50, so STRONG_SELL beats weak BUY."""
    return abs(Decimal(str(signal_data["signal_strength"])) - Decimal("50"))


def _should_call_ai(all_signals: list[dict]) -> tuple[bool, str]:
    """
    ⚠️ SIGNAL FILTER: Only call AI when signals are strong enough to warrant analysis.

    Returns:
        (should_call: bool, reason: str)

    Conditions to call AI:
    1. At least 3 signals align in direction (all BUY or all SELL)
    2. OR: At least one signal has confidence > 75%
    3. Don't call if all signals are HOLD
    """
    if not all_signals:
        return False, "No signals available"

    # Count signal directions
    buy_count = sum(1 for s in all_signals if s.get("signal_type") == "BUY")
    sell_count = sum(1 for s in all_signals if s.get("signal_type") == "SELL")
    hold_count = sum(1 for s in all_signals if s.get("signal_type") == "HOLD")

    # Condition 1: At least 3 signals align
    if buy_count >= 3:
        return True, f"Strong convergence: {buy_count} BUY signals"
    if sell_count >= 3:
        return True, f"Strong convergence: {sell_count} SELL signals"

    # Condition 2: At least one signal with very high confidence
    max_confidence = max((s.get("confidence", 0) for s in all_signals), default=0)
    if max_confidence > 75:
        return True, f"High confidence signal detected: {max_confidence}%"

    # Condition 3: Don't call if all weak signals
    if hold_count == len(all_signals):
        return False, "All signals are HOLD"

    # Mixed signals with low confidence - don't call
    if buy_count == 1 or sell_count == 1:
        return False, f"Weak/mixed signals: {buy_count} BUY, {sell_count} SELL, {hold_count} HOLD"

    return False, "Signals don't meet AI analysis criteria"


@celery_app.task(name="calculate_portfolio_stats")
def calculate_portfolio_stats():
    """
    Calculate portfolio statistics for all watchlists.
    Runs every hour.
    """
    import asyncio

    try:
        asyncio.run(_calculate_portfolio_stats_async())
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error calculating portfolio stats: {e}")
        raise


async def _calculate_portfolio_stats_async():
    """Async implementation of portfolio stats calculation."""
    task_name = "calculate_portfolio_stats"
    start_time = time.time()
    session = await get_async_session()

    try:
        await TaskLoggingHandler.log_task_start(db=session, task_name=task_name)

        # Get all watchlists with items. Watchlist.is_active is a Python property,
        # not a SQL expression, so filtering happens after loading.
        stmt = select(Watchlist)
        result = await session.execute(stmt)
        watchlists = [
            watchlist for watchlist in result.scalars().all() if watchlist.symbols
        ]

        logger.info(f"Calculating stats for {len(watchlists)} watchlists")

        notifier = TelegramNotifier()

        for watchlist in watchlists:
            # Get latest prices for all symbols
            current_prices = {}
            for symbol in watchlist.symbols:
                stmt = (
                    select(OHLCVCandle)
                    .where(
                        OHLCVCandle.watchlist_id == watchlist.id,
                        OHLCVCandle.symbol == symbol,
                    )
                    .order_by(OHLCVCandle.close_time.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                candle = result.scalar_one_or_none()
                if candle:
                    current_prices[symbol] = candle.close_price

            # Update portfolio stats
            stats = await PortfolioManager.update_portfolio_stats(
                session=session,
                watchlist_id=watchlist.id,
                current_prices=current_prices,
            )

            # Send Telegram notification
            if stats:
                await notifier.send_portfolio_update(
                    total_invested=float(stats.total_invested),
                    current_value=float(stats.current_value),
                    total_pnl=float(
                        stats.realized_pnl + stats.unrealized_pnl
                    ),
                    return_percent=float(stats.total_return_percent),
                    win_rate=float(stats.win_rate) if stats.win_rate else 0,
                )

            logger.info(f"Stats updated for watchlist {watchlist.id}")

        await session.commit()

        duration_ms = int((time.time() - start_time) * 1000)
        await TaskLoggingHandler.log_task_completion(
            db=session,
            task_name=task_name,
            duration_ms=duration_ms,
            success=True,
            metadata={"watchlists": len(watchlists)},
        )
        await session.commit()

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Error in _calculate_portfolio_stats_async: {e}")
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
