"""
Analysis service: orchestrates indicator calculation and AI-powered analysis.
Supports Claude, OpenAI, and DeepSeek APIs.
"""

import logging
from uuid import UUID

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.modules.analysis.models import AIAnalysisResult, AnalysisType, TradeAction
from app.modules.analysis.indicators import calculate_indicators
from app.modules.analysis.multi_ai_provider import analyze_with_ai
from app.modules.market.service import MarketService
from app.modules.strategy.service import StrategyService

logger = logging.getLogger("cloud_ai_trading.analysis")


class AnalysisService:

    @staticmethod
    async def run_analysis(
        db: AsyncSession,
        user_id: UUID,
        symbol: str,
        exchange_type: str = "binance",
        analysis_type: AnalysisType = AnalysisType.MANUAL,
        strategy_id: UUID | None = None,
    ) -> AIAnalysisResult:
        """
        Run a full AI analysis pipeline:
        1. Fetch candle data from exchange
        2. Calculate technical indicators
        3. Optionally load user strategy params
        4. Call Claude API for analysis
        5. Store results
        """
        # Step 1: Fetch candle data (1h, 100 candles for indicator calculation)
        candles = await MarketService.get_candles(symbol, "1h", 100)
        if not candles or len(candles) < 30:
            raise ValueError(f"Insufficient candle data for {symbol} (need at least 30)")

        # Step 2: Calculate indicators
        indicators = calculate_indicators(candles)

        # Step 3: Load strategy params if provided
        user_strategy = None
        if strategy_id:
            try:
                strategy = await StrategyService.get_strategy(db, user_id, strategy_id)
                user_strategy = {
                    "risk_level": "medium",
                    "holding_period": strategy.timeframe,
                    "stop_loss_pct": float(strategy.stop_loss_pct),
                    "take_profit_pct": float(strategy.take_profit_pct),
                    "position_size_pct": strategy.position_sizing.get("value", 5.0),
                }
            except Exception:
                pass

        # Step 4: Call AI API (Claude, OpenAI, or DeepSeek - determined by settings.AI_PROVIDER)
        ai_result = await analyze_with_ai(
            symbol=symbol,
            indicators=indicators,
            user_strategy=user_strategy,
        )

        # Step 5: Store result
        action_str = ai_result.get("action", "HOLD").upper()
        try:
            action = TradeAction(action_str.lower())
        except ValueError:
            action = TradeAction.HOLD

        analysis = AIAnalysisResult(
            user_id=user_id,
            symbol=symbol,
            exchange_type=exchange_type,
            analysis_type=analysis_type,
            indicators_snapshot=indicators,
            claude_response=ai_result,  # Still named claude_response for backward compatibility
            action=action,
            confidence=ai_result.get("confidence", 0),
            entry_price=ai_result.get("entry_price"),
            stop_loss=ai_result.get("stop_loss"),
            take_profit=ai_result.get("take_profit"),
            risk_reward_ratio=ai_result.get("risk_reward_ratio"),
            prompt_used=ai_result.get("prompt_used", ""),
            tokens_used=ai_result.get("tokens_used", 0),
            api_cost=ai_result.get("api_cost", 0),
        )
        db.add(analysis)
        await db.flush()
        await db.refresh(analysis)
        return analysis

    @staticmethod
    async def get_analyses(
        db: AsyncSession,
        user_id: UUID,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[AIAnalysisResult]:
        """Get analysis history."""
        query = (
            select(AIAnalysisResult)
            .where(AIAnalysisResult.user_id == user_id)
        )
        if symbol:
            query = query.where(AIAnalysisResult.symbol == symbol)
        query = query.order_by(desc(AIAnalysisResult.created_at)).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_analysis(
        db: AsyncSession, user_id: UUID, analysis_id: UUID
    ) -> AIAnalysisResult:
        """Get a specific analysis."""
        result = await db.execute(
            select(AIAnalysisResult).where(
                AIAnalysisResult.id == analysis_id,
                AIAnalysisResult.user_id == user_id,
            )
        )
        analysis = result.scalar_one_or_none()
        if not analysis:
            raise NotFoundException("Analysis")
        return analysis

    @staticmethod
    async def get_latest_analysis(
        db: AsyncSession, user_id: UUID, symbol: str
    ) -> AIAnalysisResult | None:
        """Get the most recent analysis for a symbol."""
        result = await db.execute(
            select(AIAnalysisResult)
            .where(
                AIAnalysisResult.user_id == user_id,
                AIAnalysisResult.symbol == symbol,
            )
            .order_by(desc(AIAnalysisResult.created_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_summary(db: AsyncSession, user_id: UUID) -> dict:
        """Get analysis usage summary."""
        result = await db.execute(
            select(
                func.count(AIAnalysisResult.id),
                func.coalesce(func.sum(AIAnalysisResult.api_cost), 0),
                func.coalesce(func.sum(AIAnalysisResult.tokens_used), 0),
            ).where(AIAnalysisResult.user_id == user_id)
        )
        row = result.one()
        total_analyses, total_cost, total_tokens = row

        # Count by action
        actions_result = await db.execute(
            select(AIAnalysisResult.action, func.count(AIAnalysisResult.id))
            .where(AIAnalysisResult.user_id == user_id)
            .group_by(AIAnalysisResult.action)
        )
        action_counts = {r[0].value: r[1] for r in actions_result.all()}

        # Average confidence
        avg_result = await db.execute(
            select(func.avg(AIAnalysisResult.confidence))
            .where(AIAnalysisResult.user_id == user_id)
        )
        avg_confidence = avg_result.scalar() or 0

        return {
            "total_analyses": total_analyses,
            "total_cost": float(total_cost),
            "total_tokens": int(total_tokens),
            "buy_signals": action_counts.get("buy", 0),
            "sell_signals": action_counts.get("sell", 0),
            "hold_signals": action_counts.get("hold", 0),
            "avg_confidence": round(float(avg_confidence), 1),
        }
