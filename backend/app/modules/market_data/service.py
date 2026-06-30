"""Market data service for OHLCV candles and technical indicators."""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.market_data.indicators import TechnicalIndicatorCalculator, IndicatorAnalyzer
from app.modules.market_data.models import OHLCVCandle, TechnicalIndicator, MarketDataEvent
from app.modules.market_data.schemas import (
    CandleWithIndicators,
    OHLCVCandleResponse,
    OHLCVHistoryRequest,
    TechnicalIndicatorsResponse,
)
from app.modules.watchlist.models import Watchlist

logger = logging.getLogger(__name__)


class MarketDataService:
    """Service for managing market data."""

    @staticmethod
    async def save_ohlcv_candle(
        session: AsyncSession,
        watchlist_id: UUID,
        symbol: str,
        timeframe: str,
        candle_data: dict,
    ) -> Optional[OHLCVCandle]:
        """Save OHLCV candle to database."""
        try:
            # Check if candle already exists
            stmt = select(OHLCVCandle).where(
                and_(
                    OHLCVCandle.watchlist_id == watchlist_id,
                    OHLCVCandle.symbol == symbol,
                    OHLCVCandle.timeframe == timeframe,
                    OHLCVCandle.open_time == candle_data["open_time"],
                )
            )
            existing = await session.execute(stmt)
            existing_candle = existing.scalar_one_or_none()

            if existing_candle:
                # Update existing candle
                existing_candle.open_price = Decimal(str(candle_data["open"]))
                existing_candle.high_price = Decimal(str(candle_data["high"]))
                existing_candle.low_price = Decimal(str(candle_data["low"]))
                existing_candle.close_price = Decimal(str(candle_data["close"]))
                existing_candle.volume = Decimal(str(candle_data["volume"]))
                existing_candle.quote_volume = Decimal(str(candle_data["quote_volume"]))
                existing_candle.trades_count = candle_data.get("trades_count")
                existing_candle.taker_buy_base_volume = (
                    Decimal(str(candle_data["taker_buy_base_volume"]))
                    if candle_data.get("taker_buy_base_volume")
                    else None
                )
                existing_candle.taker_buy_quote_volume = (
                    Decimal(str(candle_data["taker_buy_quote_volume"]))
                    if candle_data.get("taker_buy_quote_volume")
                    else None
                )
                existing_candle.updated_at = datetime.now(timezone.utc)
                await session.flush()
                return existing_candle

            # Create new candle
            candle = OHLCVCandle(
                watchlist_id=watchlist_id,
                symbol=symbol,
                timeframe=timeframe,
                open_time=candle_data["open_time"],
                close_time=candle_data["close_time"],
                open_price=Decimal(str(candle_data["open"])),
                high_price=Decimal(str(candle_data["high"])),
                low_price=Decimal(str(candle_data["low"])),
                close_price=Decimal(str(candle_data["close"])),
                volume=Decimal(str(candle_data["volume"])),
                quote_volume=Decimal(str(candle_data["quote_volume"])),
                trades_count=candle_data.get("trades_count"),
                taker_buy_base_volume=(
                    Decimal(str(candle_data["taker_buy_base_volume"]))
                    if candle_data.get("taker_buy_base_volume")
                    else None
                ),
                taker_buy_quote_volume=(
                    Decimal(str(candle_data["taker_buy_quote_volume"]))
                    if candle_data.get("taker_buy_quote_volume")
                    else None
                ),
            )
            session.add(candle)
            await session.flush()
            return candle

        except Exception as e:
            logger.error(f"Error saving OHLCV candle: {e}")
            return None

    @staticmethod
    async def calculate_and_save_indicators(
        session: AsyncSession,
        watchlist_id: UUID,
        symbol: str,
        timeframe: str,
        limit: int = 200,
    ) -> Optional[TechnicalIndicator]:
        """Calculate technical indicators for the latest candle."""
        try:
            # Get recent candles
            stmt = (
                select(OHLCVCandle)
                .where(
                    and_(
                        OHLCVCandle.watchlist_id == watchlist_id,
                        OHLCVCandle.symbol == symbol,
                        OHLCVCandle.timeframe == timeframe,
                    )
                )
                .order_by(desc(OHLCVCandle.open_time))
                .limit(limit)
            )
            result = await session.execute(stmt)
            candles = list(reversed(result.scalars().all()))

            if not candles:
                logger.warning(f"No candles found for {symbol} {timeframe}")
                return None

            # Extract price data for indicator calculation
            closes = [Decimal(c.close_price) for c in candles]
            highs = [Decimal(c.high_price) for c in candles]
            lows = [Decimal(c.low_price) for c in candles]

            # Calculate indicators
            calc = TechnicalIndicatorCalculator
            latest_candle = candles[-1]

            ema_12 = calc.calculate_ema(closes, 12)
            ema_26 = calc.calculate_ema(closes, 26)
            ema_50 = calc.calculate_ema(closes, 50)
            ema_200 = calc.calculate_ema(closes, 200)

            rsi_14 = calc.calculate_rsi(closes, 14)

            atr_14 = calc.calculate_atr(highs, lows, closes, 14)

            bb_upper, bb_middle, bb_lower, bb_width, bb_position = calc.calculate_bollinger_bands(closes, 20, 2.0)

            macd, macd_signal, macd_histogram = calc.calculate_macd(closes, 12, 26, 9)

            # Check if indicators already exist for this candle
            stmt = select(TechnicalIndicator).where(
                TechnicalIndicator.ohlcv_candle_id == latest_candle.id
            )
            result = await session.execute(stmt)
            existing_indicators = result.scalar_one_or_none()

            if existing_indicators:
                # Update existing indicators
                existing_indicators.ema_12 = ema_12
                existing_indicators.ema_26 = ema_26
                existing_indicators.ema_50 = ema_50
                existing_indicators.ema_200 = ema_200
                existing_indicators.rsi_14 = rsi_14
                existing_indicators.atr_14 = atr_14
                existing_indicators.bb_upper = bb_upper
                existing_indicators.bb_middle = bb_middle
                existing_indicators.bb_lower = bb_lower
                existing_indicators.bb_width = bb_width
                existing_indicators.bb_position = bb_position
                existing_indicators.macd = macd
                existing_indicators.macd_signal = macd_signal
                existing_indicators.macd_histogram = macd_histogram
                existing_indicators.updated_at = datetime.now(timezone.utc)
                await session.flush()
                return existing_indicators

            # Create new indicators
            indicators = TechnicalIndicator(
                watchlist_id=watchlist_id,
                ohlcv_candle_id=latest_candle.id,
                symbol=symbol,
                timeframe=timeframe,
                timestamp=latest_candle.close_time,
                ema_12=ema_12,
                ema_26=ema_26,
                ema_50=ema_50,
                ema_200=ema_200,
                rsi_14=rsi_14,
                atr_14=atr_14,
                bb_upper=bb_upper,
                bb_middle=bb_middle,
                bb_lower=bb_lower,
                bb_width=bb_width,
                bb_position=bb_position,
                macd=macd,
                macd_signal=macd_signal,
                macd_histogram=macd_histogram,
            )
            session.add(indicators)
            await session.flush()
            return indicators

        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return None

    @staticmethod
    async def get_ohlcv_history(
        session: AsyncSession,
        watchlist_id: UUID,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        start_time: Optional[datetime] = None,
    ) -> List[OHLCVCandleResponse]:
        """Get OHLCV history for a symbol."""
        try:
            query = select(OHLCVCandle).where(
                and_(
                    OHLCVCandle.watchlist_id == watchlist_id,
                    OHLCVCandle.symbol == symbol,
                    OHLCVCandle.timeframe == timeframe,
                )
            )

            if start_time:
                query = query.where(OHLCVCandle.open_time >= start_time)

            query = query.order_by(desc(OHLCVCandle.open_time)).limit(limit)

            result = await session.execute(query)
            candles = list(reversed(result.scalars().all()))

            return [
                OHLCVCandleResponse(
                    id=c.id,
                    watchlist_id=c.watchlist_id,
                    symbol=c.symbol,
                    timeframe=c.timeframe,
                    open_time=c.open_time,
                    close_time=c.close_time,
                    open=c.open_price,
                    high=c.high_price,
                    low=c.low_price,
                    close=c.close_price,
                    volume=c.volume,
                    quote_volume=c.quote_volume,
                    trades_count=c.trades_count,
                    taker_buy_base_volume=c.taker_buy_base_volume,
                    taker_buy_quote_volume=c.taker_buy_quote_volume,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
                for c in candles
            ]

        except Exception as e:
            logger.error(f"Error fetching OHLCV history: {e}")
            return []

    @staticmethod
    async def get_candle_with_indicators(
        session: AsyncSession,
        watchlist_id: UUID,
        symbol: str,
        timeframe: str,
    ) -> Optional[CandleWithIndicators]:
        """Get latest candle with indicators."""
        try:
            # Get latest candle
            stmt = (
                select(OHLCVCandle)
                .where(
                    and_(
                        OHLCVCandle.watchlist_id == watchlist_id,
                        OHLCVCandle.symbol == symbol,
                        OHLCVCandle.timeframe == timeframe,
                    )
                )
                .order_by(desc(OHLCVCandle.open_time))
                .limit(1)
            )
            result = await session.execute(stmt)
            candle = result.scalar_one_or_none()

            if not candle:
                return None

            # Get indicators for this candle
            stmt = select(TechnicalIndicator).where(
                TechnicalIndicator.ohlcv_candle_id == candle.id
            )
            result = await session.execute(stmt)
            indicators = result.scalar_one_or_none()

            candle_response = OHLCVCandleResponse(
                id=candle.id,
                watchlist_id=candle.watchlist_id,
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open_time=candle.open_time,
                close_time=candle.close_time,
                open=candle.open_price,
                high=candle.high_price,
                low=candle.low_price,
                close=candle.close_price,
                volume=candle.volume,
                quote_volume=candle.quote_volume,
                trades_count=candle.trades_count,
                taker_buy_base_volume=candle.taker_buy_base_volume,
                taker_buy_quote_volume=candle.taker_buy_quote_volume,
                created_at=candle.created_at,
                updated_at=candle.updated_at,
            )

            indicators_response = None
            if indicators:
                indicators_response = TechnicalIndicatorsResponse(
                    id=indicators.id,
                    ohlcv_candle_id=indicators.ohlcv_candle_id,
                    symbol=indicators.symbol,
                    timeframe=indicators.timeframe,
                    timestamp=indicators.timestamp,
                    ema_12=indicators.ema_12,
                    ema_26=indicators.ema_26,
                    ema_50=indicators.ema_50,
                    ema_200=indicators.ema_200,
                    rsi_14=indicators.rsi_14,
                    macd=indicators.macd,
                    macd_signal=indicators.macd_signal,
                    macd_histogram=indicators.macd_histogram,
                    atr_14=indicators.atr_14,
                    bb_upper=indicators.bb_upper,
                    bb_middle=indicators.bb_middle,
                    bb_lower=indicators.bb_lower,
                    bb_width=indicators.bb_width,
                    bb_position=indicators.bb_position,
                    created_at=indicators.created_at,
                    updated_at=indicators.updated_at,
                )

            return CandleWithIndicators(candle=candle_response, indicators=indicators_response)

        except Exception as e:
            logger.error(f"Error fetching candle with indicators: {e}")
            return None

    @staticmethod
    async def save_market_data_event(
        session: AsyncSession,
        watchlist_id: UUID,
        symbol: str,
        event_type: str,
        price: Optional[Decimal] = None,
        event_data: Optional[dict] = None,
    ) -> Optional[MarketDataEvent]:
        """Save a market data event."""
        try:
            event = MarketDataEvent(
                watchlist_id=watchlist_id,
                symbol=symbol,
                event_type=event_type,
                price=price,
                event_data=event_data,
            )
            session.add(event)
            await session.flush()
            return event

        except Exception as e:
            logger.error(f"Error saving market data event: {e}")
            return None

    @staticmethod
    async def cleanup_old_candles(
        session: AsyncSession,
        watchlist_id: UUID,
        symbol: str,
        timeframe: str,
        days: int = 90,
    ) -> int:
        """Delete old OHLCV candles older than specified days."""
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

            stmt = select(OHLCVCandle).where(
                and_(
                    OHLCVCandle.watchlist_id == watchlist_id,
                    OHLCVCandle.symbol == symbol,
                    OHLCVCandle.timeframe == timeframe,
                    OHLCVCandle.open_time < cutoff_date,
                )
            )
            result = await session.execute(stmt)
            candles = result.scalars().all()

            count = len(candles)
            for candle in candles:
                await session.delete(candle)

            await session.flush()
            return count

        except Exception as e:
            logger.error(f"Error cleaning up candles: {e}")
            return 0
