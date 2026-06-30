"""
P1 Signal Generation Tests
Tests for MACD and Bollinger Band signal generation
"""

import pytest
from decimal import Decimal
import sys
import asyncio
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.modules.trading.signals import TradingSignalGenerator


class TestMACDSignalGeneration:
    """Test MACD signal generation logic."""

    @pytest.mark.asyncio
    async def test_macd_bullish_crossover(self):
        """Test bullish MACD crossover detection (Golden Cross)."""
        signal = await TradingSignalGenerator.generate_macd_signal(
            macd=Decimal("150"),
            macd_signal=Decimal("100"),
            prev_macd=Decimal("50"),
            prev_macd_signal=Decimal("100"),
        )

        assert signal["signal_type"] == "STRONG_BUY"
        assert signal["signal_strength"] == Decimal("100")
        assert signal["confidence"] == Decimal("90")
        assert signal["strategy"] == "MACD"
        assert "crossover" in signal["recommendation"].lower()

    @pytest.mark.asyncio
    async def test_macd_bearish_crossover(self):
        """Test bearish MACD crossover detection (Death Cross)."""
        signal = await TradingSignalGenerator.generate_macd_signal(
            macd=Decimal("50"),
            macd_signal=Decimal("100"),
            prev_macd=Decimal("150"),
            prev_macd_signal=Decimal("100"),
        )

        assert signal["signal_type"] == "STRONG_SELL"
        assert signal["signal_strength"] == Decimal("0")
        assert signal["confidence"] == Decimal("90")
        assert signal["strategy"] == "MACD"

    @pytest.mark.asyncio
    async def test_macd_bullish_trend_no_crossover(self):
        """Test MACD bullish trend without crossover."""
        signal = await TradingSignalGenerator.generate_macd_signal(
            macd=Decimal("150"),
            macd_signal=Decimal("100"),
            prev_macd=Decimal("140"),
            prev_macd_signal=Decimal("95"),
        )

        assert signal["signal_type"] == "BUY"
        assert Decimal("60") <= signal["signal_strength"] <= Decimal("75")
        assert Decimal("70") <= signal["confidence"] <= Decimal("80")
        assert signal["strategy"] == "MACD"

    @pytest.mark.asyncio
    async def test_macd_bearish_trend_no_crossover(self):
        """Test MACD bearish trend without crossover."""
        signal = await TradingSignalGenerator.generate_macd_signal(
            macd=Decimal("50"),
            macd_signal=Decimal("100"),
            prev_macd=Decimal("60"),
            prev_macd_signal=Decimal("105"),
        )

        assert signal["signal_type"] == "SELL"
        assert Decimal("25") <= signal["signal_strength"] <= Decimal("40")
        assert Decimal("70") <= signal["confidence"] <= Decimal("80")
        assert signal["strategy"] == "MACD"

    @pytest.mark.asyncio
    async def test_macd_indicators_used_included(self):
        """Test that MACD and signal values are stored in indicators_used."""
        signal = await TradingSignalGenerator.generate_macd_signal(
            macd=Decimal("150"),
            macd_signal=Decimal("100"),
            prev_macd=Decimal("50"),
            prev_macd_signal=Decimal("100"),
        )

        assert "indicators_used" in signal
        assert signal["indicators_used"]["MACD"] == 150.0
        assert signal["indicators_used"]["MACD_Signal"] == 100.0
        assert "distance" in signal["indicators_used"]


class TestBollingerBandSignalGeneration:
    """Test Bollinger Band signal generation logic."""

    @pytest.mark.asyncio
    async def test_bb_upper_breakout(self):
        """Test upper Bollinger Band breakout detection."""
        signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("105"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        assert signal["signal_type"] == "STRONG_BUY"
        assert signal["signal_strength"] == Decimal("100")
        assert signal["confidence"] == Decimal("85")
        assert signal["strategy"] == "BOLLINGER_BAND"
        assert "breakout" in signal["recommendation"].lower()

    @pytest.mark.asyncio
    async def test_bb_lower_breakout(self):
        """Test lower Bollinger Band breakout detection."""
        signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("3"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        assert signal["signal_type"] == "STRONG_SELL"
        assert signal["signal_strength"] == Decimal("0")
        assert signal["confidence"] == Decimal("85")
        assert signal["strategy"] == "BOLLINGER_BAND"

    @pytest.mark.asyncio
    async def test_bb_price_near_upper_band(self):
        """Test price near upper band detection."""
        signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("98"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        assert signal["signal_type"] == "BUY"
        assert Decimal("65") <= signal["signal_strength"] <= Decimal("75")
        assert signal["confidence"] == Decimal("75")
        assert signal["strategy"] == "BOLLINGER_BAND"

    @pytest.mark.asyncio
    async def test_bb_price_near_lower_band(self):
        """Test price near lower band detection."""
        signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("6.5"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        assert signal["signal_type"] == "SELL"
        assert Decimal("25") <= signal["signal_strength"] <= Decimal("40")
        assert signal["confidence"] == Decimal("75")
        assert signal["strategy"] == "BOLLINGER_BAND"

    @pytest.mark.asyncio
    async def test_bb_price_in_middle(self):
        """Test price in middle band range."""
        # Price in upper half
        signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("70"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        assert signal["signal_type"] == "HOLD"
        assert signal["signal_strength"] == Decimal("55")
        assert signal["confidence"] == Decimal("60")

        # Price in lower half
        signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("30"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        assert signal["signal_type"] == "HOLD"
        assert signal["signal_strength"] == Decimal("45")
        assert signal["confidence"] == Decimal("60")

    @pytest.mark.asyncio
    async def test_bb_indicators_used_included(self):
        """Test that BB values are stored in indicators_used."""
        signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("105"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        assert "indicators_used" in signal
        assert signal["indicators_used"]["Price"] == 105.0
        assert signal["indicators_used"]["BB_Upper"] == 100.0
        assert signal["indicators_used"]["BB_Middle"] == 50.0
        assert signal["indicators_used"]["BB_Lower"] == 5.0
        assert signal["indicators_used"]["Band_Width"] == 95.0


class TestSignalStructure:
    """Test that all signals have consistent structure."""

    @pytest.mark.asyncio
    async def test_signal_structure_consistency(self):
        """Verify all signals return consistent dict structure."""
        macd_signal = await TradingSignalGenerator.generate_macd_signal(
            macd=Decimal("150"),
            macd_signal=Decimal("100"),
            prev_macd=None,
            prev_macd_signal=None,
        )

        bb_signal = await TradingSignalGenerator.generate_bb_breakout_signal(
            current_price=Decimal("105"),
            bb_upper=Decimal("100"),
            bb_middle=Decimal("50"),
            bb_lower=Decimal("5"),
        )

        required_fields = {
            "signal_type",
            "signal_strength",
            "confidence",
            "recommendation",
            "indicators_used",
            "strategy",
        }

        for signal in [macd_signal, bb_signal]:
            assert set(signal.keys()) == required_fields
            assert signal["signal_type"] in [
                "STRONG_BUY",
                "BUY",
                "HOLD",
                "SELL",
                "STRONG_SELL",
            ]
            assert Decimal("0") <= signal["signal_strength"] <= Decimal("100")
            assert Decimal("0") <= signal["confidence"] <= Decimal("100")
            assert isinstance(signal["recommendation"], str)
            assert len(signal["recommendation"]) > 0
            assert isinstance(signal["indicators_used"], dict)


if __name__ == "__main__":
    # Run tests with: pytest tests/test_p1_signals.py -v
    pytest.main([__file__, "-v"])
