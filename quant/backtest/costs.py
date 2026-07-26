"""Cost model — slippage + spread + commission (design §4.6, §11).

Costs MUST be deducted or the backtest invents an edge that doesn't exist. A long
pays UP on entry and receives LESS on exit by (slippage + half-spread). These
assumptions are later calibrated against observed paper slippage (成本校准闭环).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    slippage_bps: float = 5.0            # per-side, basis points of price
    half_spread_bps: float = 3.0         # half the bid/ask spread, per side
    commission_per_share: float = 0.0    # US equities/ETF ~ 0

    @property
    def _side_frac(self) -> float:
        return (self.slippage_bps + self.half_spread_bps) / 10_000.0

    def entry_fill(self, price: float) -> float:
        """Effective buy price (worse = higher)."""
        return price * (1.0 + self._side_frac)

    def exit_fill(self, price: float) -> float:
        """Effective sell price (worse = lower)."""
        return price * (1.0 - self._side_frac)

    def commission(self, shares: float) -> float:
        return abs(shares) * self.commission_per_share
