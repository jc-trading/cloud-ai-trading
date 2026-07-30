"""Recommendation-quality scoring (Direction v3): how good are the platform's
three answers — entry recommendations (scored from trades elsewhere), trend
PHASE labels, and top/bottom ZONE bands — measured against what actually
happened next. This is the new heart of the G1 report (拍板 2026-07-30: 判据从
「能不能自动赚钱」改成「推荐/阶段判断/区间准不准」).

Research layer: reads bars via get_bars (I/O allowed here, engine stays pure).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from quant.data import bars as barsmod
from quant.engine import indicators as ind
from quant.engine import phase as phasemod
from quant.engine import zones as zonesmod


def _daily(symbol: str, start, end) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return barsmod.get_bars(symbol, "1d", start=start, end=end, adjust="split_div")


# --- phase accuracy --------------------------------------------------------

def phase_accuracy(symbols: list[str], start, end, *, horizon: int = 20,
                   params: phasemod.PhaseParams = phasemod.PhaseParams(),
                   progress=lambda *_: None) -> dict:
    """For every symbol-day, compare the phase label to the FORWARD return over
    `horizon` sessions. An honest phase read means: up-days skew positive,
    down-days skew negative, range-days sit in between with smaller magnitude."""
    buckets: dict[str, list[np.ndarray]] = {p: [] for p in
                                            (phasemod.PHASE_UP, phasemod.PHASE_DOWN,
                                             phasemod.PHASE_RANGE)}
    used = 0
    for n, sym in enumerate(symbols, 1):
        b = _daily(sym, start, end)
        if len(b) < horizon + 30:
            continue
        close = b["close"].reset_index(drop=True)
        labels = phasemod.classify(close, params)["phase"]
        fwd = close.shift(-horizon) / close - 1.0
        ok = fwd.notna()
        used += 1
        for p in buckets:
            v = fwd[ok & (labels == p)].to_numpy()
            if len(v):
                buckets[p].append(v)
        if n % 100 == 0:
            progress(f"phase accuracy: {n}/{len(symbols)} symbols")

    out: dict = {"horizon_days": horizon, "symbols_used": used, "per_phase": {}}
    for p, chunks in buckets.items():
        if not chunks:
            continue
        v = np.concatenate(chunks)
        out["per_phase"][p] = {
            "days": int(len(v)),
            "hit_rate_positive_fwd": float((v > 0).mean()),
            "median_fwd_return": float(np.median(v)),
            "mean_fwd_return": float(v.mean()),
        }
    up = out["per_phase"].get(phasemod.PHASE_UP, {})
    down = out["per_phase"].get(phasemod.PHASE_DOWN, {})
    out["separation_median"] = (up.get("median_fwd_return", 0.0)
                                - down.get("median_fwd_return", 0.0))
    return out


# --- zone quality ----------------------------------------------------------

def zone_quality(symbols: list[str], start, end, *, horizon: int = 40,
                 step: int = 21, atr_period: int = 14,
                 params: zonesmod.ZoneParams = zonesmod.ZoneParams(),
                 progress=lambda *_: None) -> dict:
    """At monthly checkpoints, compute the as-of zones and score the NEXT
    `horizon` sessions: was the band touched, and once touched, did price
    respect it (fail to CLOSE beyond the far edge) or break through?"""
    res_stats = {"published": 0, "touched": 0, "respected": 0, "broke": 0}
    sup_stats = {"published": 0, "touched": 0, "respected": 0, "broke": 0}

    for n, sym in enumerate(symbols, 1):
        b = _daily(sym, start, end)
        if len(b) < 120 + horizon:
            continue
        high = b["high"].reset_index(drop=True)
        low = b["low"].reset_index(drop=True)
        close = b["close"].reset_index(drop=True)
        atr_series = ind.atr(high, low, close, atr_period)

        for t in range(120, len(b) - horizon, step):
            a = float(atr_series.iloc[t]) if not np.isnan(atr_series.iloc[t]) else 0.0
            read = zonesmod.compute_zones(high.iloc[:t + 1], low.iloc[:t + 1],
                                          close.iloc[:t + 1], a, params)
            f_high = high.iloc[t + 1:t + 1 + horizon]
            f_low = low.iloc[t + 1:t + 1 + horizon]
            f_close = close.iloc[t + 1:t + 1 + horizon]
            if read.resistance is not None:
                z = read.resistance
                res_stats["published"] += 1
                if bool((f_high >= z.lo).any()):
                    res_stats["touched"] += 1
                    if bool((f_close > z.hi).any()):
                        res_stats["broke"] += 1
                    else:
                        res_stats["respected"] += 1
            if read.support is not None:
                z = read.support
                sup_stats["published"] += 1
                if bool((f_low <= z.hi).any()):
                    sup_stats["touched"] += 1
                    if bool((f_close < z.lo).any()):
                        sup_stats["broke"] += 1
                    else:
                        sup_stats["respected"] += 1
        if n % 50 == 0:
            progress(f"zone quality: {n}/{len(symbols)} symbols")

    def _rates(s):
        return {
            **s,
            "touch_rate": s["touched"] / s["published"] if s["published"] else 0.0,
            "respect_given_touch": s["respected"] / s["touched"] if s["touched"] else 0.0,
        }

    return {"horizon_days": horizon, "checkpoint_step": step,
            "resistance": _rates(res_stats), "support": _rates(sup_stats)}
