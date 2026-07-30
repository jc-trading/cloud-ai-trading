"""Cost model — slippage + spread + commission (design §4.6, §11).

Costs MUST be deducted or the backtest invents an edge that doesn't exist. A long
pays UP on entry and receives LESS on exit by (slippage + half-spread). These
assumptions are later calibrated against observed paper slippage (成本校准闭环).

The §7 spread GATE cannot run on daily bars (no bid/ask), so per 拍板 2026-07-30
it is approximated here as an ADV-tiered half-spread penalty — thinner names pay
more per side — and the real bid/ask pre-order gate lands in R1 (paper checks).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    slippage_bps: float = 5.0            # per-side, basis points of price
    half_spread_bps: float = 3.0         # fallback half-spread when ADV unknown
    # (min dollar ADV, half-spread bps) — first matching tier wins, descending.
    # Starting points only; calibrated against observed paper slippage later.
    spread_tiers_bps: tuple[tuple[float, float], ...] = (
        (500e6, 2.0),      # mega-liquid: pennies wide
        (100e6, 4.0),
        (0.0, 8.0),        # at the $20M ADV funnel floor: meaningfully wider
    )
    commission_per_share: float = 0.0    # US equities/ETF ~ 0

    def half_spread_for(self, adv: float | None) -> float:
        if adv is None:
            return self.half_spread_bps
        for min_adv, bps in self.spread_tiers_bps:
            if adv >= min_adv:
                return bps
        return self.spread_tiers_bps[-1][1]

    def _side_frac(self, adv: float | None = None) -> float:
        return (self.slippage_bps + self.half_spread_for(adv)) / 10_000.0

    def entry_fill(self, price: float, adv: float | None = None) -> float:
        """Effective buy price (worse = higher)."""
        return price * (1.0 + self._side_frac(adv))

    def exit_fill(self, price: float, adv: float | None = None) -> float:
        """Effective sell price (worse = lower)."""
        return price * (1.0 - self._side_frac(adv))

    def commission(self, shares: float) -> float:
        return abs(shares) * self.commission_per_share
