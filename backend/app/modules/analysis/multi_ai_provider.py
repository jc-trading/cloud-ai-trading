"""
Multi-AI Provider Support for Trading Analysis.

Supports Claude, OpenAI, and DeepSeek with unified interface.
Provider and model can be switched via environment variables.
"""

import json
import logging
from typing import Optional

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger("cloud_ai_trading.multi_ai_provider")


ANALYSIS_SYSTEM_PROMPT = """You are a professional cryptocurrency and stock trading analyst.
You analyze market data, technical indicators, and provide trading recommendations.
Always respond in valid JSON format with the exact structure requested.
Be objective and data-driven. Consider risk management in every recommendation.
Never guarantee profits. Always include risk warnings when confidence is below 70."""


def build_analysis_prompt(
    symbol: str,
    indicators: dict,
    candle_patterns: Optional[str] = None,
    market_sentiment: Optional[str] = None,
    user_strategy: Optional[dict] = None,
) -> str:
    """Build the analysis prompt for AI analysis."""

    prompt = f"""Analyze the following market data for **{symbol}** and provide a comprehensive trading recommendation.

## Technical Indicators
- RSI(14): {indicators.get('rsi', 'N/A')}
- MACD Line: {indicators.get('macd_line', 'N/A')}, Signal: {indicators.get('macd_signal', 'N/A')}, Histogram: {indicators.get('macd_histogram', 'N/A')}
- EMA(12): {indicators.get('ema_12', 'N/A')}, EMA(26): {indicators.get('ema_26', 'N/A')}
- Bollinger Bands: Upper {indicators.get('bb_upper', 'N/A')}, Middle {indicators.get('bb_middle', 'N/A')}, Lower {indicators.get('bb_lower', 'N/A')}
- Current Price: {indicators.get('current_price', 'N/A')}
- 24h Volume: {indicators.get('volume', 'N/A')}
- 24h Change: {indicators.get('change_24h', 'N/A')}%
"""

    # Include all 4 signals for multi-strategy analysis
    all_signals = indicators.get('all_signals', {})
    if all_signals:
        prompt += f"""
## Multi-Strategy Signal Analysis
- **Momentum (EMA Crossover):** {all_signals.get('momentum', {}).get('type', 'N/A')} (strength: {all_signals.get('momentum', {}).get('strength', 'N/A')}%, confidence: {all_signals.get('momentum', {}).get('confidence', 'N/A')}%)
- **Contrarian (RSI Levels):** {all_signals.get('contrarian', {}).get('type', 'N/A')} (strength: {all_signals.get('contrarian', {}).get('strength', 'N/A')}%, confidence: {all_signals.get('contrarian', {}).get('confidence', 'N/A')}%)
- **MACD Crossover:** {all_signals.get('macd', {}).get('type', 'N/A')} (strength: {all_signals.get('macd', {}).get('strength', 'N/A')}%, confidence: {all_signals.get('macd', {}).get('confidence', 'N/A')}%)
- **Bollinger Band Breakout:** {all_signals.get('bollinger_band', {}).get('type', 'N/A')} (strength: {all_signals.get('bollinger_band', {}).get('strength', 'N/A')}%, confidence: {all_signals.get('bollinger_band', {}).get('confidence', 'N/A')}%)

Please analyze the **convergence** and **divergence** of these signals:
- Do all signals align? (convergence = higher confidence)
- Are there conflicting signals? (divergence = caution needed)
- Which signals are strongest?
- What is the consensus direction?
"""

    if candle_patterns:
        prompt += f"\n## Candlestick Patterns\n{candle_patterns}\n"

    if market_sentiment:
        prompt += f"\n## Market Sentiment\n{market_sentiment}\n"

    if user_strategy:
        prompt += f"""
## User Strategy Parameters
- Risk Tolerance: {user_strategy.get('risk_level', 'medium')}
- Holding Period: {user_strategy.get('holding_period', 'short-term')}
- Stop Loss: {user_strategy.get('stop_loss_pct', 3)}%
- Take Profit: {user_strategy.get('take_profit_pct', 8)}%
- Max Position Size: {user_strategy.get('position_size_pct', 5)}% of portfolio
"""

    prompt += """
## Required Output (JSON)
Respond with ONLY a valid JSON object, no markdown formatting:
{
  "action": "BUY" or "SELL" or "HOLD",
  "confidence": 0-100,
  "reason": "Brief analysis summary in 2-3 sentences",
  "entry_price": number or null,
  "stop_loss": number or null,
  "take_profit": number or null,
  "risk_reward_ratio": number or null,
  "key_factors": ["factor1", "factor2", "factor3"],
  "risk_warning": "Any risk considerations"
}"""

    return prompt


async def analyze_with_ai(
    symbol: str,
    indicators: dict,
    candle_patterns: Optional[str] = None,
    market_sentiment: Optional[str] = None,
    user_strategy: Optional[dict] = None,
) -> Optional[dict]:
    """
    Call AI provider (Claude, OpenAI, or DeepSeek) for trading analysis.

    Provider is determined by settings.AI_PROVIDER environment variable.

    Returns:
        Analysis dict when AI succeeds, otherwise None.
    """
    provider = settings.AI_PROVIDER.lower()

    if provider == "claude":
        return await _analyze_with_claude(
            symbol, indicators, candle_patterns, market_sentiment, user_strategy
        )
    elif provider == "openai":
        return await _analyze_with_openai(
            symbol, indicators, candle_patterns, market_sentiment, user_strategy
        )
    elif provider == "deepseek":
        return await _analyze_with_deepseek(
            symbol, indicators, candle_patterns, market_sentiment, user_strategy
        )
    else:
        logger.error(f"Unknown AI provider: {provider}")
        return None


async def _analyze_with_claude(
    symbol: str,
    indicators: dict,
    candle_patterns: Optional[str] = None,
    market_sentiment: Optional[str] = None,
    user_strategy: Optional[dict] = None,
) -> Optional[dict]:
    """Call Claude API for analysis."""
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not configured; skipping Claude analysis")
        return None

    prompt = build_analysis_prompt(
        symbol, indicators, candle_patterns, market_sentiment, user_strategy
    )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=15.0,
        )

        message = await client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=512,  # Reduced from 1024, JSON doesn't need that much
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text.strip()

        # Parse JSON
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response_text[start:end])
            else:
                logger.error(f"Failed to parse Claude response: {response_text[:200]}")
                return None

        # Add metadata
        result["tokens_used"] = message.usage.input_tokens + message.usage.output_tokens
        result["api_cost"] = _estimate_claude_cost(message.usage)
        result["provider"] = "claude"

        return result

    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return None


async def _analyze_with_openai(
    symbol: str,
    indicators: dict,
    candle_patterns: Optional[str] = None,
    market_sentiment: Optional[str] = None,
    user_strategy: Optional[dict] = None,
) -> Optional[dict]:
    """Call OpenAI API for analysis."""
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not configured; skipping OpenAI analysis")
        return None

    prompt = build_analysis_prompt(
        symbol, indicators, candle_patterns, market_sentiment, user_strategy
    )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=15.0,
        )

        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=512,
            temperature=0.7,
        )

        response_text = response.choices[0].message.content.strip()

        # Parse JSON
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response_text[start:end])
            else:
                logger.error(f"Failed to parse OpenAI response: {response_text[:200]}")
                return None

        # Add metadata
        result["tokens_used"] = response.usage.prompt_tokens + response.usage.completion_tokens
        result["api_cost"] = _estimate_openai_cost(response.usage)
        result["provider"] = "openai"

        return result

    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None


async def _analyze_with_deepseek(
    symbol: str,
    indicators: dict,
    candle_patterns: Optional[str] = None,
    market_sentiment: Optional[str] = None,
    user_strategy: Optional[dict] = None,
) -> Optional[dict]:
    """Call DeepSeek API for analysis."""
    if not settings.DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not configured; skipping DeepSeek analysis")
        return None

    prompt = build_analysis_prompt(
        symbol, indicators, candle_patterns, market_sentiment, user_strategy
    )

    try:
        from openai import AsyncOpenAI

        # DeepSeek uses OpenAI-compatible API
        client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
            timeout=15.0,
        )

        response = await client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=512,
            temperature=0.7,
        )

        response_text = response.choices[0].message.content.strip()

        # Parse JSON
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response_text[start:end])
            else:
                logger.error(f"Failed to parse DeepSeek response: {response_text[:200]}")
                return None

        # Add metadata
        result["tokens_used"] = response.usage.prompt_tokens + response.usage.completion_tokens
        result["api_cost"] = _estimate_deepseek_cost(response.usage)
        result["provider"] = "deepseek"

        return result

    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        return None


def _estimate_claude_cost(usage) -> float:
    """Estimate Claude API cost (Haiku pricing)."""
    # Claude Haiku: $0.80/MTok input, $4/MTok output
    input_cost = usage.input_tokens * 0.80 / 1_000_000
    output_cost = usage.output_tokens * 4 / 1_000_000
    return round(input_cost + output_cost, 6)


def _estimate_openai_cost(usage) -> float:
    """Estimate OpenAI API cost (gpt-4o-mini pricing)."""
    # gpt-4o-mini: $0.15/MTok input, $0.60/MTok output
    input_cost = usage.prompt_tokens * 0.15 / 1_000_000
    output_cost = usage.completion_tokens * 0.60 / 1_000_000
    return round(input_cost + output_cost, 6)


def _estimate_deepseek_cost(usage) -> float:
    """Estimate DeepSeek API cost."""
    # DeepSeek-chat: $0.27/MTok input, $1.1/MTok output (approximate)
    input_cost = usage.prompt_tokens * 0.27 / 1_000_000
    output_cost = usage.completion_tokens * 1.1 / 1_000_000
    return round(input_cost + output_cost, 6)
