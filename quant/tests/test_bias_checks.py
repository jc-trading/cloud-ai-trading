"""R0-8: lookahead/recursive bias detection catches a planted future function and
passes the real engine."""

import numpy as np
import pandas as pd

from quant.backtest import bias_checks
from quant.engine.strategy import StrategyParams, compute_signals


def _series(n=200):
    ts = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    rng = np.sin(np.linspace(0, 12, n)) * 8 + np.linspace(100, 160, n)  # trend + wave
    return pd.DataFrame({
        "ts": ts, "open": rng, "high": rng * 1.01, "low": rng * 0.99,
        "close": rng, "volume": [1e6] * n, "vwap": rng, "trade_count": [1000] * n,
    })


def test_clean_engine_has_no_lookahead():
    bars = _series()
    p = StrategyParams()
    rep = bias_checks.check_lookahead(bars, compute_signals, p)
    bias_checks.check_recursive(bars, compute_signals, p, report=rep)
    assert rep.clean, rep.summary()
    assert rep.checked > 0


def test_planted_future_function_is_caught():
    bars = _series()
    p = StrategyParams()

    def biased(b, params):
        df = compute_signals(b, params)
        # peek one bar ahead: atr[i] = close[i+1] (future). Truncating the history
        # changes the last row, which the check must detect.
        future = b["close"].shift(-1).reset_index(drop=True)
        future = future.fillna(b["close"].reset_index(drop=True))
        df = df.copy()
        df["atr"] = future.values
        return df

    rep = bias_checks.check_lookahead(bars, biased, p)
    assert not rep.clean
    assert any(col == "atr" for _, _, col, _, _ in rep.violations)
