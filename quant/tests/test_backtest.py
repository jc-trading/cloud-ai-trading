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


def test_cost_spread_tiers_by_adv():
    # 拍板 2026-07-30: the §7 spread gate is approximated as an ADV-tiered
    # half-spread — thinner names pay more per side
    c = CostModel(slippage_bps=5)
    assert c.entry_fill(100.0, adv=1e9) == pytest.approx(100.07)    # 5+2 bps
    assert c.entry_fill(100.0, adv=200e6) == pytest.approx(100.09)  # 5+4 bps
    assert c.entry_fill(100.0, adv=25e6) == pytest.approx(100.13)   # 5+8 bps
    assert c.exit_fill(100.0, adv=25e6) == pytest.approx(99.87)
    assert c.entry_fill(100.0) == pytest.approx(100.08)             # None -> fallback


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


def _ohlc_bars(dates, opens, highs, lows, closes, *, vol=5_000_000):
    ts = pd.DatetimeIndex([pd.Timestamp(f"{d} 00:00", tz="America/New_York")
                           for d in dates]).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts, "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [vol] * len(dates), "vwap": closes,
        "trade_count": [1000] * len(dates),
    })


def _patch_bars(monkeypatch, frames):
    def fake_get_bars(symbol, timeframe="1d", start=None, end=None, **kw):
        df = frames.get(symbol)
        if df is None:
            return next(iter(frames.values())).iloc[0:0].copy()
        df = df.copy()
        if start is not None:
            df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
        return df
    monkeypatch.setattr(simulator.barsmod, "get_bars", fake_get_bars)


def test_gap_through_stop_fills_at_open(monkeypatch, small_params):
    # Review F3: a gap far below the stop must fill at the OPEN, not the stop —
    # the old model capped every loss at ~1R.
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=20)]
    closes = [10 + i for i in range(15)] + [12.0, 11.5, 11.0, 10.5, 10.0]
    opens = list(closes); highs = [c * 1.001 for c in closes]; lows = [c * 0.999 for c in closes]
    # day 15 gaps from ~24 straight down to 12 at the open
    opens[15] = 12.0; highs[15] = 12.5; lows[15] = 11.8
    frames = {"AAA": _ohlc_bars(sessions, opens, highs, lows, closes),
              "SPY": _synth_bars(sessions, [400] * 20)}
    _patch_bars(monkeypatch, frames)
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-01",
                              starting_capital=2000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep)
    res = simulator.run(["AAA"], {"AAA": "tech"}, cfg)
    stops = [t for t in res.trades if t.exit_reason in ("hard_stop", "trailing")]
    assert stops, "expected a stop exit on the gap day"
    t = stops[0]
    assert t.exit_price < 12.5          # filled around the 12.0 open, not near the stop
    assert t.r_multiple < -1.5          # the loss is NOT capped at ~1R


def test_same_bar_spike_does_not_trail_itself_out(monkeypatch, small_params):
    # Review F2: today's high must not raise the trailing stop that today's low
    # is then tested against (impossible intraday order).
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=18)]
    closes = [10 + i for i in range(15)] + [25.0, 25.5, 26.0]
    opens = list(closes); highs = [c * 1.001 for c in closes]; lows = [c * 0.999 for c in closes]
    # day 15: huge intraday spike (high 40) that fully retraces (low = 24.9)
    opens[15] = 24.5; highs[15] = 40.0; lows[15] = 24.3; closes[15] = 25.0
    frames = {"AAA": _ohlc_bars(sessions, opens, highs, lows, closes),
              "SPY": _synth_bars(sessions, [400] * 18)}
    _patch_bars(monkeypatch, frames)
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-01",
                              starting_capital=2000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep)
    res = simulator.run(["AAA"], {"AAA": "tech"}, cfg)
    spike_day = pd.Timestamp(sessions[15]).date()
    assert not any(t.exit_date == spike_day for t in res.trades), \
        "spike-day high raised a trailing stop that its own low then hit (lookahead)"


def test_entry_day_stop_is_checked(monkeypatch, small_params):
    # Review F4: a stop can be blown the same day the position opens.
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=17)]
    # flat until day 13, single-day breakout so the FIRST up-signal lands on
    # day 14's close -> entry at day 15's open, which then crashes intraday
    closes = [10.0] * 14 + [11.0, 12.0, 11.5]
    opens = list(closes); highs = [c * 1.001 for c in closes]; lows = [c * 0.999 for c in closes]
    opens[15] = 12.0; highs[15] = 12.1; lows[15] = 5.0; closes[15] = 6.0
    frames = {"AAA": _ohlc_bars(sessions, opens, highs, lows, closes),
              "SPY": _synth_bars(sessions, [400] * 17)}
    _patch_bars(monkeypatch, frames)
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-01",
                              starting_capital=2000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep)
    res = simulator.run(["AAA"], {"AAA": "tech"}, cfg)
    same_day = [t for t in res.trades if t.entry_date == t.exit_date]
    assert same_day, "entry-day stop breach was not exited until the next day"


def test_data_end_forces_close(monkeypatch, small_params):
    # Review F6: a symbol whose data ends mid-window must be force-closed at the
    # last available close, not marked at cost forever (delisting != breakeven).
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=25)]
    # AAA trends up (entry), then its data STOPS after day 17 while SPY continues
    aaa_dates = sessions[:18]
    closes = [10 + i for i in range(15)] + [25.0, 24.0, 20.0]
    frames = {"AAA": _synth_bars(aaa_dates, closes),
              "SPY": _synth_bars(sessions, [400] * 25)}
    # BBB keeps the trading-day calendar alive to the window end
    frames["BBB"] = _synth_bars(sessions, [50.0] * 25)
    _patch_bars(monkeypatch, frames)
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-15",
                              starting_capital=2000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep)
    res = simulator.run(["AAA", "BBB"], {"AAA": "tech", "BBB": "fin"}, cfg)
    ends = [t for t in res.trades if t.exit_reason == "data_end"]
    stops = [t for t in res.trades if t.symbol == "AAA" and t.exit_reason in ("hard_stop", "trailing")]
    assert ends or stops, "AAA position neither stopped out nor force-closed at data end"
    if ends:
        assert ends[0].exit_price <= 20.0   # last available close, cost-deducted


def _protection_scenario(monkeypatch, small_params, **overrides):
    """AAA trends up, gaps down hard on day 12 (big daily equity loss + drawdown).
    BBB's ONLY up-signal is day 12's close -> its entry would fill day 13. With
    protections on, that entry must be blocked; with them off, BBB trades."""
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=22)]
    aaa_closes = [10 + i * 0.5 for i in range(12)] + [9.8] + [9.5] * 9
    aaa_opens = list(aaa_closes); aaa_highs = [c * 1.001 for c in aaa_closes]
    aaa_lows = [c * 0.999 for c in aaa_closes]
    aaa_opens[12] = 10.0; aaa_lows[12] = 9.5          # -35% gap through the stop
    bbb_closes = [10.0] * 12 + [11.0, 10.2] + [10.0] * 8
    bbb_opens = list(bbb_closes); bbb_highs = [c * 1.001 for c in bbb_closes]
    bbb_lows = [c * 0.999 for c in bbb_closes]
    bbb_opens[13] = 11.0; bbb_lows[13] = 9.8          # if entered: same-day stop -> a trade
    frames = {"AAA": _ohlc_bars(sessions, aaa_opens, aaa_highs, aaa_lows, aaa_closes),
              "BBB": _ohlc_bars(sessions, bbb_opens, bbb_highs, bbb_lows, bbb_closes),
              "SPY": _synth_bars(sessions, [400] * 22)}
    _patch_bars(monkeypatch, frames)
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-01",
                              starting_capital=2000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep, **overrides)
    return simulator.run(["AAA", "BBB"], {"AAA": "tech", "BBB": "fin"}, cfg)


def test_daily_loss_pause_blocks_next_day_entries(monkeypatch, small_params):
    # control: protections effectively off -> BBB is entered (and stops out)
    res = _protection_scenario(monkeypatch, small_params,
                               daily_loss_pause_pct=1.0, drawdown_halt_pct=1.0)
    assert any(t.symbol == "BBB" for t in res.trades), "control run should trade BBB"
    # with the daily-loss pause armed, the day after the -2%+ equity day is frozen
    res = _protection_scenario(monkeypatch, small_params,
                               daily_loss_pause_pct=0.02, drawdown_halt_pct=1.0)
    assert not any(t.symbol == "BBB" for t in res.trades), \
        "entry filled the day after a daily-loss-pause breach"


def test_drawdown_halt_blocks_entries(monkeypatch, small_params):
    res = _protection_scenario(monkeypatch, small_params,
                               daily_loss_pause_pct=1.0, drawdown_halt_pct=0.05)
    assert not any(t.symbol == "BBB" for t in res.trades), \
        "entry filled while portfolio drawdown exceeded the halt threshold"


def test_etf_slot_is_extra_not_ladder(monkeypatch, small_params):
    # A4-Extra (拍板 2026-07-30): an ETF held early must NOT consume one of the
    # stock ladder slots. SPY enters first; three stocks break out later — all
    # three must still fit the 3-slot ladder (cash-capped sizes are fine).
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=20)]

    def crashing(base, rise_from, rise_until, step, rng):
        closes = [base if i < rise_from else
                  base + (min(i, rise_until) - rise_from + 1) * step
                  for i in range(16)] + [base * 0.55] * 4
        opens = list(closes); highs = [c * (1 + rng) for c in closes]
        lows = [c * (1 - rng) for c in closes]
        opens[16] = base * 0.6; lows[16] = base * 0.5     # gap-crash day 16
        return _ohlc_bars(sessions, opens, highs, lows, closes, vol=50_000_000)

    # SPY trends days 0-8 (enters ~day 7-9), then drifts just below cost so it
    # neither pyramids nor exits; wide-range stocks break out day 10 — the risk
    # cap (not the ladder cash cap) sizes them, so three positions fit the
    # remaining cash iff the slot gate lets them in
    spy_closes = ([400 + 2 * i for i in range(9)]          # 400..416
                  + [416 - 0.2 * i for i in range(1, 8)]   # gentle drift down
                  + [240.0] * 4)                           # crash
    spy_opens = list(spy_closes)
    spy_highs = [c * 1.001 for c in spy_closes]; spy_lows = [c * 0.999 for c in spy_closes]
    spy_opens[16] = 250.0; spy_lows[16] = 238.0
    frames = {"SPY": _ohlc_bars(sessions, spy_opens, spy_highs, spy_lows, spy_closes,
                                vol=50_000_000)}
    for s in ("S1", "S2", "S3"):
        frames[s] = crashing(10.0, 10, 15, 0.7, 0.05)
    _patch_bars(monkeypatch, frames)
    cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-01",
                              starting_capital=3000, adv_window=2,
                              strategy=sp, funnel=fp, exits=ep)
    res = simulator.run(["S1", "S2", "S3", "SPY"],
                        {"S1": "tech", "S2": "fin", "S3": "ind"}, cfg)
    traded = {t.symbol for t in res.trades}
    assert "SPY" in traded, "whitelisted ETF never entered its extra slot"
    assert {"S1", "S2", "S3"} <= traded, \
        "an ETF holding consumed a stock ladder slot (A4-Extra violated)"


def test_membership_gate_blocks_pre_inclusion_entries(monkeypatch, small_params):
    # Review B3: a symbol must not be tradable before its index-inclusion date.
    sp, fp, ep = small_params
    sessions = [d.date().isoformat() for d in pd.bdate_range("2024-01-01", periods=20)]
    dates = [pd.Timestamp(s).date() for s in sessions]
    closes = [10 + i for i in range(15)] + [24, 18, 12, 9, 8]   # entry then crash
    frames = {"AAA": _synth_bars(sessions, closes),
              "SPY": _synth_bars(sessions, [400] * 20)}
    _patch_bars(monkeypatch, frames)

    def run_with(membership):
        cfg = simulator.SimConfig(start="2024-01-01", end="2024-02-01",
                                  starting_capital=2000, adv_window=2,
                                  strategy=sp, funnel=fp, exits=ep,
                                  membership_on=membership)
        return simulator.run(["AAA"], {"AAA": "tech"}, cfg)

    # control: member the whole window -> trades
    assert run_with(lambda d: {"AAA"}).trades
    # joins the index only after the crash -> never tradable in the window
    join = dates[17]
    res = run_with(lambda d: {"AAA"} if d >= join else set())
    assert not res.trades, "entered a symbol before its index-inclusion date"


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
