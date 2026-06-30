"""
Analysis service: orchestrates indicator calculation and AI-powered analysis.
Supports Claude, OpenAI, and DeepSeek APIs.
"""

import logging
from uuid import UUID

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.modules.analysis.models import AIAnalysisResult, AnalysisType, TradeAction, Verdict
from app.modules.analysis.indicators import calculate_indicators
from app.modules.analysis.multi_ai_provider import analyze_with_ai
from app.modules.market.service import MarketService
from app.modules.strategy.service import StrategyService

logger = logging.getLogger("cloud_ai_trading.analysis")

# Shared reason string for the no-AI / no-data no-go Decision (acceptance criterion 1).
NO_AI_VERDICT_REASON = "AI 未调用/key缺失/数据不足"


def _derive_verdict(action: TradeAction, confidence: int) -> Verdict:
    """Happy-path verdict from the AI's action + confidence.

    GO only on a directional call (BUY/SELL) with enough conviction; otherwise
    WATCH (neutral / non-committal). NO_GO is reserved for the AI-unavailable /
    data-missing paths and is set explicitly there, never derived here.
    """
    if action in (TradeAction.BUY, TradeAction.SELL) and confidence >= 60:
        return Verdict.GO
    return Verdict.WATCH


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
            # No indicators possible. Don't raise (that path was silently swallowed
            # by the caller's try/except = hidden breakage); persist exactly one
            # no-go Decision so every cycle still records a row.
            return await AnalysisService._persist_no_ai_decision(
                db,
                user_id=user_id,
                symbol=symbol,
                exchange_type=exchange_type,
                analysis_type=analysis_type,
                indicators={},
                data_completeness={"indicators": False, "ai_output": False},
                verdict_reason=(
                    f"{NO_AI_VERDICT_REASON}: insufficient candle data for {symbol} "
                    "(need >= 30)"
                ),
                ai_skip_reason="insufficient_candle_data",
            )

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

        # None-safe: analyze_with_ai returns None on missing key / API error /
        # parse failure. Without this guard, ai_result.get(...) below raises
        # AttributeError, which the caller's try/except swallows -> the cycle
        # stores nothing (hidden breakage). Instead, persist one no-go Decision.
        if ai_result is None:
            return await AnalysisService._persist_no_ai_decision(
                db,
                user_id=user_id,
                symbol=symbol,
                exchange_type=exchange_type,
                analysis_type=analysis_type,
                indicators=indicators,
                data_completeness={"indicators": True, "ai_output": False},
                verdict_reason=NO_AI_VERDICT_REASON,
                ai_skip_reason="ai_unavailable",
            )

        # Step 5: Store result (happy path — AI was invoked and returned a result)
        action_str = ai_result.get("action", "HOLD").upper()
        try:
            action = TradeAction(action_str.lower())
        except ValueError:
            action = TradeAction.HOLD

        confidence = ai_result.get("confidence", 0) or 0

        analysis = AIAnalysisResult(
            user_id=user_id,
            symbol=symbol,
            exchange_type=exchange_type,
            analysis_type=analysis_type,
            indicators_snapshot=indicators,
            claude_response=ai_result,  # Still named claude_response for backward compatibility
            action=action,
            confidence=confidence,
            entry_price=ai_result.get("entry_price"),
            stop_loss=ai_result.get("stop_loss"),
            take_profit=ai_result.get("take_profit"),
            risk_reward_ratio=ai_result.get("risk_reward_ratio"),
            prompt_used=ai_result.get("prompt_used", ""),
            tokens_used=ai_result.get("tokens_used", 0),
            api_cost=ai_result.get("api_cost", 0),
            # Decision fields: AI was invoked; verdict derived from action/confidence.
            verdict=_derive_verdict(action, confidence),
            verdict_reason=ai_result.get("reason"),
            data_completeness={"indicators": True, "ai_output": True},
            ai_invoked=True,
            ai_skip_reason=None,
        )
        db.add(analysis)
        await db.flush()
        await db.refresh(analysis)
        return analysis

    @staticmethod
    async def _persist_no_ai_decision(
        db: AsyncSession,
        *,
        user_id: UUID,
        symbol: str,
        exchange_type: str,
        analysis_type: AnalysisType,
        indicators: dict,
        data_completeness: dict,
        verdict_reason: str,
        ai_skip_reason: str,
    ) -> AIAnalysisResult:
        """Persist exactly one no-go Decision for the AI-unavailable / no-data path.

        Keeps the per-cycle invariant (one Decision per symbol per cycle) without
        raising. action=HOLD + verdict=NO_GO (HOLD != no-go elsewhere, but here the
        decision genuinely is "don't act, we couldn't analyze"), ai_invoked=False,
        and data_completeness flags what was missing.
        """
        analysis = AIAnalysisResult(
            user_id=user_id,
            symbol=symbol,
            exchange_type=exchange_type,
            analysis_type=analysis_type,
            indicators_snapshot=indicators,
            claude_response={},
            action=TradeAction.HOLD,
            confidence=0,
            verdict=Verdict.NO_GO,
            verdict_reason=verdict_reason,
            data_completeness=data_completeness,
            ai_invoked=False,
            ai_skip_reason=ai_skip_reason,
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
