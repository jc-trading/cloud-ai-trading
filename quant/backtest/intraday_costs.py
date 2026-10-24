"""1min cost model (P5 D-7) — the daily CostModel with a one-tick floor per side.

At 1min scale a few basis points of a cheap stock round to less than the
smallest price increment, so each side pays at least ``min_tick``. Uncalibrated:
every 1m report card says so.
"""

from __future__ import annotations

from dataclasses import dataclass

from quant import config
from quant.backtest.costs import CostModel


@dataclass(frozen=True)
class IntradayCostModel(CostModel):
    min_tick: float = config.INTRADAY_MIN_TICK

    def _side_cost(self, price: float, adv: float | None = None) -> float:
        return max(price * (self.slippage_bps + self.half_spread_for(adv)) / 1e4, self.min_tick)

    def entry_fill(self, price: float, adv: float | None = None) -> float:
        return price + self._side_cost(price, adv)

    def exit_fill(self, price: float, adv: float | None = None) -> float:
        return price - self._side_cost(price, adv)
