"""Exit stack — pure decision functions (design §6.6; A1/A3).

Long-only, trend-following, NO max holding days: let winners run, but never let a
stalled name hog a slot. Priority (highest first):
  1 hard stop        broker-side floor, ~3% equity risk at entry
  2 protective trail  primary take-profit — trail after ~1.5-2R, no fixed target
  3 reversal         real reversal (not a pullback): MA break + Down persistence,
                     or a predicted drop below cost -> protect profit
  4 stagnation       held long, underperforming the hurdle, signal no longer strong
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from quant import config
from quant.engine.signal import Direction


@dataclass
class Position:
    symbol: str
    shares: float
    avg_cost: float
    stop: float                      # current (broker-side) stop price
    r_unit: float                    # initial per-share risk (entry - initial stop)
    entry_date: date
    high_water: float                # highest close seen since entry
    adds_done: int = 0
    reversal_count: int = 0          # consecutive bars looking like a real reversal
    bars_held: int = 0

    @property
    def unrealized_r(self) -> float:
        """Profit in R multiples at the high-water mark."""
        if self.r_unit <= 0:
            return 0.0
        return (self.high_water - self.avg_cost) / self.r_unit


@dataclass(frozen=True)
class ExitParams:
    trailing_start_r: float = config.TRAILING_START_R   # [C8] start trailing at ~1.5-2R
    trailing_atr_mult: float = 3.0                       # [C8] trail distance in ATRs
    reversal_bars: int = 3                               # [C5] Down persistence to confirm
    stagnation_bars: int = 30                            # [C6] ~6 weeks of sessions
    stagnation_hurdle: float = 0.0                       # [C6] vs benchmark return since entry


@dataclass(frozen=True)
class ExitDecision:
    action: str          # 'hard_stop' | 'trailing' | 'reversal' | 'stagnation'
    price: float
    reason: str


def trailing_stop(high_water: float, atr: float, mult: float) -> float:
    return high_water - mult * atr


def update_position_bar(pos: Position, bar_close: float, bar_high: float,
                        signal_direction: Direction, below_ma: bool) -> None:
    """Advance per-bar state (high-water, reversal counter, bars held). Call once
    per bar BEFORE evaluate_exit. Mutates pos in place (the simulator owns it)."""
    pos.bars_held += 1
    pos.high_water = max(pos.high_water, bar_high, bar_close)
    if signal_direction is Direction.DOWN and below_ma:
        pos.reversal_count += 1
    else:
        pos.reversal_count = 0


def maybe_raise_trailing(pos: Position, atr: float, params: ExitParams) -> None:
    """Once in enough profit, ratchet the stop up to the trailing level (never
    down). This is the primary take-profit — no fixed target price."""
    if pos.unrealized_r >= params.trailing_start_r:
        candidate = trailing_stop(pos.high_water, atr, params.trailing_atr_mult)
        pos.stop = max(pos.stop, candidate)


def evaluate_exit(pos: Position, bar_low: float, bar_close: float, *,
                  signal_direction: Direction, expected_move: float,
                  params: ExitParams = ExitParams(),
                  benchmark_return_since_entry: float | None = None) -> ExitDecision | None:
    """Return the first exit that fires in priority order, else None."""
    # 1 hard stop / trailing stop (both are the resting stop being breached)
    if bar_low <= pos.stop:
        # distinguish label: if stop is above avg_cost it's a protective/trailing exit
        action = "trailing" if pos.stop >= pos.avg_cost else "hard_stop"
        return ExitDecision(action, pos.stop, f"low {bar_low:.2f} <= stop {pos.stop:.2f}")

    # 3 reversal: confirmed Down persistence, OR predicted drop below cost
    if pos.reversal_count >= params.reversal_bars:
        return ExitDecision("reversal", bar_close,
                            f"Down persisted {pos.reversal_count} bars (MA broken)")
    if signal_direction is Direction.DOWN and (bar_close - expected_move) < pos.avg_cost \
            and bar_close > pos.avg_cost:
        # still in profit but the next expected move would breach cost -> protect it
        return ExitDecision("reversal", bar_close,
                            "predicted move would drop below cost — protect profit")

    # 4 stagnation: held long, not beating the hurdle, signal no longer strong
    if pos.bars_held >= params.stagnation_bars and signal_direction is not Direction.UP:
        if benchmark_return_since_entry is not None:
            own = (bar_close - pos.avg_cost) / pos.avg_cost
            if own - benchmark_return_since_entry <= params.stagnation_hurdle:
                return ExitDecision("stagnation", bar_close,
                                    f"held {pos.bars_held} bars, lagging hurdle")
        else:
            return ExitDecision("stagnation", bar_close,
                                f"held {pos.bars_held} bars, signal faded")
    return None
