"""Top/bottom zone bands — pure functions (Direction v3 拍板 2026-07-30).

The v3 platform publishes a resistance ZONE above and a support ZONE below the
current price — honest ranges (e.g. "$185–192"), never a fake-precise target.
Method is fully deterministic and backtestable (zone hit/respect rate is scored
in R0-9): swing highs/lows -> cluster nearby levels -> band = cluster spread
padded by an ATR fraction.

No lookahead: a swing point is only CONFIRMED swing_window bars after it forms
(you cannot know a peak is a peak until price has fallen away from it), so
levels inside the last swing_window bars are never used.

Blue-sky honesty: at an all-time high there IS no resistance above — the read
returns None rather than inventing a level (default NO, no guessing).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ZoneParams:
    swing_window: int = 5          # fractal half-width; also the confirm delay
    max_lookback: int = 250        # only swings from the last ~1y of sessions
    cluster_atr_mult: float = 0.75 # levels within this*ATR merge into one zone
    pad_atr_mult: float = 0.25     # band padding around the cluster's extremes


@dataclass(frozen=True)
class Zone:
    lo: float
    hi: float
    touches: int                   # how many swing levels formed this zone

    def to_dict(self) -> dict:
        return {"lo": self.lo, "hi": self.hi, "touches": self.touches}


@dataclass(frozen=True)
class ZoneRead:
    resistance: Zone | None        # nearest zone ABOVE the close (None = blue sky)
    support: Zone | None           # nearest zone BELOW the close
    swing_highs: tuple = field(default_factory=tuple)   # evidence levels used
    swing_lows: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "resistance": self.resistance.to_dict() if self.resistance else None,
            "support": self.support.to_dict() if self.support else None,
            "swing_highs": list(self.swing_highs), "swing_lows": list(self.swing_lows),
        }


def swing_points(high: pd.Series, low: pd.Series, k: int) -> tuple[list[float], list[float]]:
    """CONFIRMED swing highs/lows: bar i is a swing high if high[i] is the max
    of highs[i-k .. i+k]; it only exists once bar i+k has printed, so the last
    k bars can never contribute a level."""
    n = len(high)
    highs: list[float] = []
    lows: list[float] = []
    hv, lv = high.to_numpy(dtype=float), low.to_numpy(dtype=float)
    for i in range(k, n - k):
        w_h = hv[i - k:i + k + 1]
        w_l = lv[i - k:i + k + 1]
        if hv[i] == w_h.max() and (w_h.argmax() == k):
            highs.append(float(hv[i]))
        if lv[i] == w_l.min() and (w_l.argmin() == k):
            lows.append(float(lv[i]))
    return highs, lows


def _cluster(levels: list[float], tol: float) -> list[list[float]]:
    """Greedy 1-D clustering: sorted levels within tol of the cluster mean merge."""
    out: list[list[float]] = []
    for lv in sorted(levels):
        if out and abs(lv - float(np.mean(out[-1]))) <= tol:
            out[-1].append(lv)
        else:
            out.append([lv])
    return out


def _zone_from(cluster: list[float], pad: float) -> Zone:
    return Zone(lo=min(cluster) - pad, hi=max(cluster) + pad, touches=len(cluster))


def compute_zones(high: pd.Series, low: pd.Series, close: pd.Series,
                  atr_now: float, params: ZoneParams = ZoneParams()) -> ZoneRead:
    """Zones as of the LAST bar of the given series (pass a sliced frame to
    evaluate historically). atr_now scales cluster tolerance and band padding."""
    k = params.swing_window
    if len(close) < 2 * k + 1 or atr_now <= 0 or np.isnan(atr_now):
        return ZoneRead(resistance=None, support=None)
    high = high.iloc[-params.max_lookback:]
    low = low.iloc[-params.max_lookback:]
    c = float(close.iloc[-1])

    sw_h, sw_l = swing_points(high, low, k)
    tol = params.cluster_atr_mult * atr_now
    pad = params.pad_atr_mult * atr_now

    # nearest confirmed structure above / below the current price. Swing lows
    # that sit above price (broken supports) act as resistance too, and broken
    # swing highs below price act as support (role reversal) — pool them.
    levels = sw_h + sw_l
    above = [lv for lv in levels if lv > c]
    below = [lv for lv in levels if lv < c]

    resistance = None
    if above:
        cl = min(_cluster(above, tol), key=lambda g: min(g))   # nearest above
        resistance = _zone_from(cl, pad)
    support = None
    if below:
        cl = max(_cluster(below, tol), key=lambda g: max(g))   # nearest below
        support = _zone_from(cl, pad)
    return ZoneRead(resistance=resistance, support=support,
                    swing_highs=tuple(sw_h), swing_lows=tuple(sw_l))
