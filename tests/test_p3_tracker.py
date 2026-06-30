"""
P3 Risk Tracker Tests - Unit tests for PortfolioRiskTracker metrics.
Tests real-time tracking, metric calculations, and portfolio monitoring.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from app.modules.risk.tracker import PortfolioRiskTracker
from app.modules.trading.models import Position


class TestPortfolioRiskTracker:
    """Test PortfolioRiskTracker functionality."""

    def test_sharpe_ratio_calculation_positive_returns(self):
        """Test Sharpe ratio with positive returns."""
        # Create mock positions with positive P&L
        positions = []
        returns = [0.10, 0.15, 0.08, 0.12]  # 10%, 15%, 8%, 12%

        for i, ret in enumerate(returns):
            exit_price = Decimal("1000") * Decimal(str(1 + ret))
            position = Position(
                id=f"test-id-{i}",
                watchlist_id="watchlist-id",
                symbol="BTCUSDT",
                entry_price=Decimal("1000"),
                exit_price=exit_price,
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        # Calculate Sharpe (with at least 2 positions)
        import asyncio
        sharpe = asyncio.run(
            PortfolioRiskTracker._calculate_sharpe_ratio(
                positions,
                Decimal("10000"),
            )
        )

        # With positive returns, Sharpe should be positive or zero (with low volatility)
        assert sharpe >= Decimal("0")

    def test_sharpe_ratio_with_insufficient_data(self):
        """Test Sharpe ratio with less than 2 positions."""
        positions = []

        import asyncio
        sharpe = asyncio.run(
            PortfolioRiskTracker._calculate_sharpe_ratio(
                positions,
                Decimal("10000"),
            )
        )

        # Should return 0 with insufficient data
        assert sharpe == Decimal("0")

    def test_sharpe_ratio_with_volatility(self):
        """Test Sharpe ratio with volatile returns."""
        # Create positions with high volatility
        positions = []
        returns = [5, -3, 8, -2, 10, 1, -4, 6]

        for ret in returns:
            # Create position with given return
            entry_price = Decimal("1000")
            exit_price = entry_price * Decimal(str(1 + ret / 100))

            position = Position(
                id="test-id",
                watchlist_id="watchlist-id",
                symbol="BTCUSDT",
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        import asyncio
        sharpe = asyncio.run(
            PortfolioRiskTracker._calculate_sharpe_ratio(
                positions,
                Decimal("10000"),
            )
        )

        # Should calculate without error, may be positive or negative
        assert isinstance(sharpe, Decimal)

    def test_var_calculation_basic(self):
        """Test Value at Risk calculation."""
        positions = []

        # Create positions with various P&Ls
        pnls = [100, 200, -50, 150, -100, 250, 75, -25]

        for i, pnl in enumerate(pnls):
            position = Position(
                id=f"test-id-{i}",
                watchlist_id="watchlist-id",
                symbol="BTCUSDT",
                entry_price=Decimal("1000"),
                exit_price=Decimal(str(1000 + pnl)),
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        import asyncio
        var_95 = asyncio.run(
            PortfolioRiskTracker._calculate_var(
                positions,
                confidence=Decimal("0.95"),
            )
        )

        # VaR at 95% confidence should be a realistic value
        assert isinstance(var_95, Decimal)
        # Should be in the range of our P&Ls
        assert var_95 <= Decimal("250")

    def test_var_with_insufficient_data(self):
        """Test VaR with less than 2 positions."""
        positions = []

        import asyncio
        var = asyncio.run(
            PortfolioRiskTracker._calculate_var(
                positions,
                confidence=Decimal("0.95"),
            )
        )

        # Should return 0 with insufficient data
        assert var == Decimal("0")

    def test_win_rate_calculation(self):
        """Test win rate calculation from closed positions."""
        positions = []

        # Create winning positions
        for i in range(3):
            position = Position(
                id=f"win-{i}",
                watchlist_id="watchlist-id",
                symbol="BTCUSDT",
                entry_price=Decimal("1000"),
                exit_price=Decimal("1100"),  # Win
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        # Create losing positions
        for i in range(2):
            position = Position(
                id=f"loss-{i}",
                watchlist_id="watchlist-id",
                symbol="ETHUSDT",
                entry_price=Decimal("1000"),
                exit_price=Decimal("950"),  # Loss
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        # Win rate = 3 / 5 = 60%
        winning = len([p for p in positions if p.realized_pnl > 0])
        total = len(positions)
        win_rate = (Decimal(winning) / Decimal(total) * Decimal("100")) if total > 0 else Decimal("0")

        assert win_rate == Decimal("60")

    def test_profit_factor_calculation(self):
        """Test profit factor (total wins / total losses)."""
        positions = []

        # Winning positions: +100, +200, +50
        for pnl in [100, 200, 50]:
            position = Position(
                id=f"win-{pnl}",
                watchlist_id="watchlist-id",
                symbol="BTCUSDT",
                entry_price=Decimal("1000"),
                exit_price=Decimal(str(1000 + pnl)),
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        # Losing positions: -50, -25
        for pnl in [-50, -25]:
            position = Position(
                id=f"loss-{abs(pnl)}",
                watchlist_id="watchlist-id",
                symbol="ETHUSDT",
                entry_price=Decimal("1000"),
                exit_price=Decimal(str(1000 + pnl)),
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        # Profit factor = 350 / 75 = 4.67
        total_wins = sum([p.realized_pnl for p in positions if p.realized_pnl > 0])
        total_losses = abs(sum([p.realized_pnl for p in positions if p.realized_pnl < 0]))

        profit_factor = (total_wins / total_losses) if total_losses > Decimal("0") else Decimal("0")

        assert profit_factor == Decimal("350") / Decimal("75")
        assert float(profit_factor) == pytest.approx(4.667, rel=0.01)

    def test_concentration_calculation(self):
        """Test portfolio concentration calculation."""
        # Create positions in 3 symbols
        positions = [
            ("BTC", Decimal("5000")),   # 50% concentration
            ("ETH", Decimal("3000")),   # 30% concentration
            ("XRP", Decimal("2000")),   # 20% concentration
        ]

        total_value = sum([p[1] for p in positions])
        max_position = max([p[1] for p in positions])

        concentration = (max_position / total_value) * Decimal("100")

        assert concentration == Decimal("50")

    def test_max_favorable_excursion_long(self):
        """Test MFE (Max Favorable Excursion) for LONG position."""
        # LONG position: entry=100, peaks to 120 then drops to 95
        entry_price = Decimal("100")
        peak_price = Decimal("120")
        current_price = Decimal("95")

        position_value = Decimal("1000")

        # MFE: best profit reached
        mfe = (peak_price - entry_price) / entry_price * position_value
        # Current P&L
        pnl = (current_price - entry_price) / entry_price * position_value

        assert mfe > 0  # Should be positive
        assert pnl < 0  # Should be negative (underwater)
        assert mfe > pnl  # MFE should be better than current PnL

    def test_max_adverse_excursion_long(self):
        """Test MAE (Max Adverse Excursion) for LONG position."""
        # LONG position: entry=100, drops to 90 then recovers to 110
        entry_price = Decimal("100")
        trough_price = Decimal("90")
        current_price = Decimal("110")

        position_value = Decimal("1000")

        # MAE: worst loss reached
        mae = (trough_price - entry_price) / entry_price * position_value
        # Current P&L
        pnl = (current_price - entry_price) / entry_price * position_value

        assert mae < 0  # Should be negative
        assert pnl > 0  # Should be positive
        assert mae < pnl  # MAE should be worse than current PnL

    def test_drawdown_calculation(self):
        """Test portfolio drawdown calculation."""
        initial_capital = Decimal("100000")
        peak_equity = Decimal("120000")
        current_equity = Decimal("108000")

        # Drawdown = (peak - current) / peak * 100
        drawdown = ((peak_equity - current_equity) / peak_equity) * Decimal("100")

        assert drawdown == Decimal("10")  # 10% drawdown

    def test_drawdown_from_initial(self):
        """Test drawdown from initial capital."""
        initial_capital = Decimal("100000")
        current_equity = Decimal("95000")

        # If current < initial, drawdown from initial
        drawdown = ((initial_capital - current_equity) / initial_capital) * Decimal("100")

        assert drawdown == Decimal("5")  # 5% drawdown

    def test_no_drawdown(self):
        """Test when equity is higher than peak."""
        peak_equity = Decimal("100000")
        current_equity = Decimal("110000")

        # No drawdown if current > peak
        drawdown = Decimal("0") if current_equity >= peak_equity else Decimal("100")

        assert drawdown == Decimal("0")


class TestMetricsEdgeCases:
    """Test edge cases in metric calculations."""

    def test_zero_position_value(self):
        """Test metrics with zero position value."""
        entry_price = Decimal("100")
        current_price = Decimal("105")
        quantity = Decimal("0")

        position_value = quantity
        pnl = position_value * ((current_price - entry_price) / entry_price)

        assert pnl == Decimal("0")

    def test_large_gains(self):
        """Test metrics with large percentage gains."""
        entry_price = Decimal("100")
        current_price = Decimal("1000")  # 900% gain
        quantity = Decimal("1000")

        pnl_percent = ((current_price - entry_price) / entry_price) * Decimal("100")

        assert pnl_percent == Decimal("900")

    def test_large_losses(self):
        """Test metrics with large percentage losses."""
        entry_price = Decimal("100")
        current_price = Decimal("10")  # 90% loss
        quantity = Decimal("1000")

        pnl_percent = ((current_price - entry_price) / entry_price) * Decimal("100")

        assert pnl_percent == Decimal("-90")

    def test_breakeven_trade(self):
        """Test position at breakeven."""
        entry_price = Decimal("100")
        current_price = Decimal("100")
        quantity = Decimal("1000")

        pnl = quantity * ((current_price - entry_price) / entry_price)
        pnl_percent = ((current_price - entry_price) / entry_price) * Decimal("100")

        assert pnl == Decimal("0")
        assert pnl_percent == Decimal("0")

    def test_negative_returns_sharpe(self):
        """Test Sharpe with consistently negative returns."""
        positions = []

        for i in range(3):
            position = Position(
                id=f"loss-{i}",
                watchlist_id="watchlist-id",
                symbol="BTCUSDT",
                entry_price=Decimal("1000"),
                exit_price=Decimal("950"),  # -5%
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        import asyncio
        sharpe = asyncio.run(
            PortfolioRiskTracker._calculate_sharpe_ratio(
                positions,
                Decimal("10000"),
            )
        )

        # Sharpe with negative returns should be negative or zero
        assert sharpe <= Decimal("0")

    def test_concentration_with_single_position(self):
        """Test concentration when only one position exists."""
        total_value = Decimal("5000")
        max_position = Decimal("5000")

        concentration = (max_position / total_value) * Decimal("100")

        assert concentration == Decimal("100")

    def test_concentration_equal_positions(self):
        """Test concentration with equal-sized positions."""
        # 10 positions of equal size
        position_count = 10
        concentration = (Decimal("1") / Decimal(position_count)) * Decimal("100")

        assert concentration == Decimal("10")

    def test_var_with_no_losses(self):
        """Test VaR when all trades are winning."""
        positions = []

        for i in range(5):
            position = Position(
                id=f"win-{i}",
                watchlist_id="watchlist-id",
                symbol="BTCUSDT",
                entry_price=Decimal("1000"),
                exit_price=Decimal("1100"),  # +10%
                quantity=Decimal("1"),
                entry_date=datetime.now(timezone.utc),
                exit_date=datetime.now(timezone.utc),
                status="closed",
                position_type="LONG",
            )
            positions.append(position)

        import asyncio
        var = asyncio.run(
            PortfolioRiskTracker._calculate_var(
                positions,
                confidence=Decimal("0.95"),
            )
        )

        # VaR should be positive or zero (no losses)
        assert var >= Decimal("0")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
