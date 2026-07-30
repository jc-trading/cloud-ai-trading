"""R0-6 engine tests: indicators, purity, strategy, sizing, exits, funnel."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.engine import funnel, indicators as ind, sizing
from quant.engine import strategy as strat
from quant.engine.exits import (ExitParams, Position, evaluate_exit,
                                 maybe_raise_trailing, update_position_bar)
from quant.engine.signal import Direction


# --- indicators (golden values) -------------------------------------------

def test_sma_and_atr_golden():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert ind.sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]
    high = pd.Series([10, 11, 12], dtype=float)
    low = pd.Series([9, 9, 10], dtype=float)
    close = pd.Series([9.5, 10.5, 11.5], dtype=float)
    tr = ind.true_range(high, low, close)
    # bar0: 10-9=1; bar1: max(11-9, |11-9.5|, |9-9.5|)=2; bar2: max(2, |12-10.5|, |10-10.5|)=2
    assert tr.tolist() == [1.0, 2.0, 2.0]


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 40, dtype=float))     # strictly rising
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert ind.rsi(up, 14).iloc[-1] == 100.0          # no losses
    assert ind.rsi(down, 14).iloc[-1] < 1.0


def test_logistic_and_zscore():
    assert abs(ind.logistic(0.0) - 0.5) < 1e-9
    z = ind.rolling_zscore(pd.Series([1, 2, 3, 4, 100], dtype=float), 5)
    assert z.iloc[-1] > 1.5   # the 100 is a positive outlier


# --- purity: engine imports nothing that does I/O -------------------------

def test_engine_is_pure():
    engine_dir = Path(__file__).resolve().parent.parent / "engine"
    banned = ("httpx", "requests", "sqlalchemy", "urllib", "alpaca", "sqlite3", "dotenv")
    for py in engine_dir.glob("*.py"):
        text = py.read_text()
        for b in banned:
            assert f"import {b}" not in text and f"from {b}" not in text, \
                f"{py.name} imports {b} — engine must stay pure"


# --- strategy: direction gate + confidence --------------------------------

def _series(closes):
    n = len(closes)
    ts = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "volume": [1e6] * n, "vwap": closes, "trade_count": [1000] * n,
    })


def test_strategy_direction_gate():
    n = 160
    up = _series([100 * (1.004 ** i) for i in range(n)])
    down = _series([100 * (0.996 ** i) for i in range(n)])
    sig_up = strat.evaluate(up, symbol="UP", as_of=date(2020, 8, 1))
    sig_dn = strat.evaluate(down, symbol="DN", as_of=date(2020, 8, 1))
    assert sig_up.direction is Direction.UP
    assert sig_up.confidence > 0 and sig_up.is_long_entry
    assert sig_dn.direction is Direction.DOWN
    assert sig_dn.confidence == 0 and not sig_dn.is_long_entry   # gated for entry
    assert sig_up.atr > 0 and sig_up.expected_move > 0


def test_strategy_warmup_none():
    assert strat.evaluate(_series([100] * 20), symbol="X") is None


# --- sizing (boundary cases) ----------------------------------------------

def test_position_size_slot_cap_binds():
    # $2k, entry 100, stop 95 -> risk cap 12 sh; slot cap (2000/3)/100=6.67 binds
    sh = sizing.position_size(2000, 100, 95, risk_pct=0.03, slots=3, settled_cash=2000)
    assert abs(sh - 6.6667) < 1e-3


def test_position_size_risk_cap_binds():
    # wide stop makes the risk budget the binding cap
    sh = sizing.position_size(2000, 100, 80, risk_pct=0.03, slots=3, settled_cash=2000)
    assert abs(sh - 3.0) < 1e-6   # 60 / (100-80)


def test_ladder_slots():
    assert sizing.concurrent_slots(2000) == 3
    assert sizing.concurrent_slots(4999) == 3
    assert sizing.concurrent_slots(5000) == 4
    assert sizing.concurrent_slots(25000) == 10


def test_pyramid_stop_keeps_combined_risk():
    # add to a winner, raise stop so combined entry-risk stays <= 3% of equity
    assert sizing.pyramid_allowed(avg_cost=100, current_price=110, adds_done=0)
    assert not sizing.pyramid_allowed(avg_cost=100, current_price=99, adds_done=0)
    assert not sizing.pyramid_allowed(avg_cost=100, current_price=110, adds_done=1)
    stop = sizing.raise_stop_for_combined_risk(10, avg_cost=100, equity=2000,
                                               current_stop=90, risk_pct=0.03)
    assert abs(stop - 94.0) < 1e-9              # 100 - 60/10
    combined_risk = 10 * (100 - stop)
    assert combined_risk <= 2000 * 0.03 + 1e-9   # within budget


# --- exits (priority stack) -----------------------------------------------

def _pos(**kw):
    base = dict(symbol="X", shares=10, avg_cost=100.0, stop=97.0, r_unit=3.0,
                entry_date=date(2020, 1, 1), high_water=100.0)
    base.update(kw)
    return Position(**base)


def test_hard_stop_fires_first():
    pos = _pos(stop=97.0)
    d = evaluate_exit(pos, bar_low=96.0, bar_close=96.5,
                      signal_direction=Direction.UP, expected_move=2.0)
    assert d.action == "hard_stop"


def test_trailing_raises_and_labels():
    pos = _pos(avg_cost=100, stop=97, r_unit=3, high_water=100)
    # push into profit: high-water 112 -> unrealized R = 4 > start 1.5
    update_position_bar(pos, bar_close=112, bar_high=112, signal_direction=Direction.UP, below_ma=False)
    maybe_raise_trailing(pos, atr=2.0, params=ExitParams(trailing_start_r=1.5, trailing_atr_mult=3.0))
    assert pos.stop == pytest.approx(112 - 6)   # 106, ratcheted up above cost
    d = evaluate_exit(pos, bar_low=105.9, bar_close=106.0,
                      signal_direction=Direction.UP, expected_move=2.0)
    assert d.action == "trailing"               # stop above cost -> labeled trailing


def test_reversal_persistence():
    pos = _pos(stop=90)
    p = ExitParams(reversal_bars=3)
    for _ in range(3):
        update_position_bar(pos, bar_close=101, bar_high=101, signal_direction=Direction.DOWN, below_ma=True)
    d = evaluate_exit(pos, bar_low=100, bar_close=101, signal_direction=Direction.DOWN,
                      expected_move=2.0, params=p)
    assert d.action == "reversal"


def test_stagnation():
    pos = _pos(stop=90, high_water=101)
    pos.bars_held = 40
    d = evaluate_exit(pos, bar_low=100, bar_close=101, signal_direction=Direction.FLAT,
                      expected_move=2.0, params=ExitParams(stagnation_bars=30),
                      benchmark_return_since_entry=0.05)  # lagging: own ~1% < 5%
    assert d.action == "stagnation"


# --- funnel ---------------------------------------------------------------

def _feat(rows):
    return pd.DataFrame(rows)


def test_funnel_chain_and_sector_cap():
    rows = [
        # symbol, price, adv, atr_pct, above_rising_ma20, sector, confidence, direction
        dict(symbol="A", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="tech", confidence=90, direction="up"),
        dict(symbol="B", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="tech", confidence=80, direction="up"),
        dict(symbol="C", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="tech", confidence=70, direction="up"),  # 3rd tech -> capped
        dict(symbol="D", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="fin", confidence=60, direction="up"),
        dict(symbol="E", price=3, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="fin", confidence=95, direction="up"),   # penny -> out
        dict(symbol="F", price=50, adv=1e6, atr_pct=0.03, above_rising_ma20=True, sector="ind", confidence=95, direction="up"),   # illiquid -> out
        dict(symbol="G", price=50, adv=3e7, atr_pct=0.20, above_rising_ma20=True, sector="ind", confidence=95, direction="up"),   # too volatile -> out
        dict(symbol="H", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=False, sector="ind", confidence=95, direction="up"),  # not aligned -> out
    ]
    short = funnel.build_shortlist(_feat(rows), funnel.FunnelParams(max_per_sector=2))
    assert short == ["A", "B", "D"]   # C dropped by sector cap; E/F/G/H filtered


def test_min_confidence_knob():
    # [C1]/review B4: threshold filters BELOW-threshold names even with slots free
    rows = [
        dict(symbol="A", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="tech", confidence=90, direction="up"),
        dict(symbol="B", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="fin", confidence=40, direction="up"),
    ]
    assert funnel.build_shortlist(_feat(rows), funnel.FunnelParams()) == ["A", "B"]
    short = funnel.build_shortlist(_feat(rows), funnel.FunnelParams(min_confidence=50))
    assert short == ["A"]


def test_stock_pool_excludes_whitelisted_etfs():
    # A4-Extra: SPY/QQQ must never be scored in the stock funnel
    rows = [
        dict(symbol="SPY", price=500, adv=1e9, atr_pct=0.02, above_rising_ma20=True, sector="etf", confidence=99, direction="up"),
        dict(symbol="A", price=50, adv=3e7, atr_pct=0.03, above_rising_ma20=True, sector="tech", confidence=60, direction="up"),
    ]
    assert funnel.build_shortlist(_feat(rows), funnel.FunnelParams()) == ["A"]


def test_etf_quota_separate():
    rows = [
        dict(symbol="SPY", price=500, adv=1e9, atr_pct=0.02, above_rising_ma20=True, sector="etf", confidence=70, direction="up"),
        dict(symbol="QQQ", price=400, adv=1e9, atr_pct=0.02, above_rising_ma20=True, sector="etf", confidence=85, direction="up"),
        dict(symbol="IWM", price=200, adv=1e9, atr_pct=0.02, above_rising_ma20=True, sector="etf", confidence=99, direction="up"),  # not whitelisted
    ]
    etfs = funnel.select_etfs(_feat(rows), max_slots=1)
    assert etfs == ["QQQ"]   # highest-confidence whitelisted, 1 slot
