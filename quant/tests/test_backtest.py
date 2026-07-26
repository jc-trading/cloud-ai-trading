"""R0-7 backtest tests: costs, metrics (hand-computed), walk-forward windows, and
a simulator integration scenario (entry -> stop exit) via monkeypatched get_bars."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.backtest import metrics, simulator, walkforward
from quant.backtest.costs import CostModel
from quant.backtest.metrics import Trade


# --- costs ----------------------------------------------------------------

def test_cost_fills():
    c = CostModel(slippage_bps=5, half_spread_bps=3)  # 8 bps per side
    assert c.entry_fill(100.0) == pytest.approx(100.08)
    assert c.exit_fill(100.0) == pytest.approx(99.92)


# --- metrics (hand-computed) ----------------------------------------------

def test_metrics_hand_values():
    # equity doubles over exactly one trading year -> CAGR ~100%
    eq = pd.Series(np.linspace(1000, 2000, metrics._TRADING_DAYS))
    assert metrics.cagr(eq) == pytest.approx(1.0, abs=0.02)
    # drawdown: 100 -> 120 -> 90 -> 130 ; peak 120 -> trough 90 = -25%
    dd = pd.Series([100, 120, 90, 130.0])
    assert metrics.max_drawdown(dd) == pytest.approx(-0.25)


def test_trade_r_multiple_and_aggregates():
    t_win = Trade("A", date(2020, 1, 1), date(2020, 2, 1), 100, 106, 10, 3.0, 60, "trailing")
    t_los = Trade("B", date(2020, 1, 1), date(2020, 2, 1), 100, 97, 10, 3.0, -30, "hard_stop")
    assert t_win.r_multiple == pytest.approx(2.0)   # (106-100)/3
    assert t_los.r_multiple == pytest.approx(-1.0)
    assert metrics.win_rate([t_win, t_los]) == 0.5
    assert metrics.profit_factor([t_win, t_los]) == pytest.approx(2.0)  # 60/30
    assert metrics.avg_r([t_win, t_los]) == pytest.approx(0.5)


def test_walk_forward_windows():
    w = walkforward.walk_forward_windows("2016-01-01", "2026-01-01",
                                         is_years=3, oos_years=1, step_years=1)
    assert len(w) == 7   # 2016..2019/20, ... last oos_end 2026-01-01
    assert w[0].is_start == pd.Timestamp("2016-01-01")
    assert w[0].oos_end == pd.Timestamp("2020-01-01")


# --- simulator integration (synthetic get_bars) ---------------------------

def _synth_bars(dates, closes, *, vol=5_000_000):
    n = len(closes)
    ts = pd.DatetimeIndex([pd.Timestamp(f"{d} 00:00", tz="America/New_York")
                           for d in dates]).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes], "close": closes,
        "volume": [vol] * n, "vwap": closes, "trade_count": [1000] * n,
    })


@pytest.fixture
def small_params():
    from quant.engine.strategy import StrategyParams
    from quant.engine.exits import ExitParams
    from quant.engine.funnel import FunnelParams
    sp = StrategyParams(ma_fast=2, ma_slow=3, macd_fast=2, macd_slow=3,
                        macd_signal=2, rsi_period=2, atr_period=2, zscore_window=3)
    fp = FunnelParams(min_adv=1_000_000, min_price=5, atr_pct_min=0.0, atr_pct_max=1.0)
    ep = ExitParams(trailing_start_r=1.5, trailing_atr_mult=3.0, reversal_bars=99,
                    stagnation_bars=999)
    return sp, fp, ep


def test_simulator_entry_and_stop_exit(monkeypatch, small_params):
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in
                pd.bdate_range("2024-01-01", periods=20)]
    # AAA: rises to trigger an UP entry, then crashes to blow the stop
    rise = [10 + i for i in range(15)]       # 10..24 uptrend
    crash = [24, 18, 12, 9, 8]               # sharp drop -> stop hit
    aaa = _synth_bars(sessions, rise + crash)
    spy = _synth_bars(sessions, [400] * 20)  # flat benchmark

    def fake_get_bars(symbol, timeframe="1d", start=None, end=None, **kw):
        if symbol == "AAA":
            return aaa.copy()
        if symbol == "SPY":
            df = spy.copy()
            if start is not None:
                df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
            return df
        return aaa.iloc[0:0].copy()

    monkeypatch.setattr(simulator.barsmod, "get_bars", fake_get_bars)
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-01",
                              starting_capital=2000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep)
    res = simulator.run(["AAA"], {"AAA": "tech"}, cfg)
    assert len(res.equity) > 0
    # a position was opened during the uptrend and closed on the crash
    assert len(res.trades) >= 1
    t = res.trades[0]
    assert t.symbol == "AAA"
    assert t.exit_reason in ("hard_stop", "trailing", "reversal")
    assert t.exit_date > t.entry_date
