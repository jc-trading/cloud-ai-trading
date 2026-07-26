"""Lookahead + recursive bias detection (design §4.6; freqtrade lookahead-/
recursive-analysis made runnable).

A strategy that peeks at future bars backtests beautifully and loses live. These
checks catch it mechanically:

  check_lookahead: recompute signals on a TRUNCATED history [0:k] and confirm the
    last row equals the full-series row at k-1, bar by bar. If any indicator uses
    future data (e.g. close.shift(-1)), truncation changes the value -> caught.

  check_recursive: recompute from different start offsets and confirm the value
    at a fixed recent bar converges (an indicator overly sensitive to how much
    history precedes it is a recursion-bias smell).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_CHECK_COLS = ("direction", "confidence", "atr", "macd_hist", "rsi", "ma_slow",
               "stop_distance", "expected_move")


def _equal(a, b, tol: float) -> bool:
    a_na = a is None or (isinstance(a, float) and np.isnan(a)) or pd.isna(a)
    b_na = b is None or (isinstance(b, float) and np.isnan(b)) or pd.isna(b)
    if a_na or b_na:
        return a_na and b_na
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return abs(float(a) - float(b)) <= tol


@dataclass
class BiasReport:
    checked: int = 0
    violations: list = field(default_factory=list)   # (kind, k, col, full_val, trunc_val)

    @property
    def clean(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        if self.clean:
            return f"CLEAN — {self.checked} points checked, no lookahead/recursive bias"
        lines = [f"BIAS DETECTED — {len(self.violations)} violation(s):"]
        for kind, k, col, fv, tv in self.violations[:20]:
            lines.append(f"  [{kind}] bar {k} col {col}: full={fv} trunc={tv}")
        return "\n".join(lines)


def check_lookahead(bars: pd.DataFrame, compute_fn, params, *,
                    sample_points: list[int] | None = None, tol: float = 1e-9,
                    report: BiasReport | None = None) -> BiasReport:
    report = report or BiasReport()
    full = compute_fn(bars, params)
    n = len(bars)
    if sample_points is None:
        # a spread of points past warmup
        warm = getattr(params, "warmup", 0)
        sample_points = [k for k in np.linspace(warm + 5, n, 8, dtype=int) if warm + 5 <= k <= n]
    for k in sample_points:
        trunc = compute_fn(bars.iloc[:k], params)
        if trunc.empty:
            continue
        report.checked += 1
        for col in _CHECK_COLS:
            if col not in full.columns:
                continue
            fv = full.iloc[k - 1][col]
            tv = trunc.iloc[-1][col]
            if not _equal(fv, tv, tol):
                report.violations.append(("lookahead", k, col, fv, tv))
    return report


def check_recursive(bars: pd.DataFrame, compute_fn, params, *,
                    offsets: tuple[int, ...] = (0, 50, 100), tol: float = 1e-6,
                    report: BiasReport | None = None) -> BiasReport:
    """Compare the value at the final bar computed from different start offsets.
    After enough warmup EMA-family indicators converge; large disagreement flags
    recursion sensitivity."""
    report = report or BiasReport()
    n = len(bars)
    ref = compute_fn(bars, params)
    if ref.empty:
        return report
    ref_last = ref.iloc[-1]
    for off in offsets:
        if off == 0 or off >= n:
            continue
        sub = compute_fn(bars.iloc[off:].reset_index(drop=True), params)
        if sub.empty:
            continue
        report.checked += 1
        for col in ("ma_slow", "atr", "rsi"):   # slower-converging indicators
            if col not in ref.columns:
                continue
            fv, tv = ref_last[col], sub.iloc[-1][col]
            if _equal(fv, tv, tol):
                continue
            # tolerate small relative drift; flag material divergence only
            denom = abs(float(fv)) if fv else 1.0
            if abs(float(fv) - float(tv)) / denom > 1e-3:
                report.violations.append(("recursive", off, col, fv, tv))
    return report


if __name__ == "__main__":
    import sys
    from quant.data import bars as barsmod
    from quant.engine.strategy import StrategyParams, compute_signals

    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    b = barsmod.get_bars(symbol, "1d", adjust="split_div")
    p = StrategyParams()
    rep = check_lookahead(b, compute_signals, p)
    check_recursive(b, compute_signals, p, report=rep)
    print(f"{symbol}: {rep.summary()}")
    sys.exit(0 if rep.clean else 1)
