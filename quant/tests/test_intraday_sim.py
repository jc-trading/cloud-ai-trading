"""P5a: the 1min group simulator — fills, exits, time exits, gating, sizing and
cash rounding on synthetic bars over a real session (2026-10-21) and a half
day (2025-11-28)."""

from datetime import date

import pandas as pd
import pytest

from quant.backtest.intraday import DayContext, GroupDayState, run_day, step
from quant.backtest.intraday_costs import IntradayCostModel
from quant.data import calendar
from quant.engine import sizing
from quant.engine.intraday_strategies import (ORBConfig, ORBSignal, RankParams, atr_window,
                                              orb_score)
from quant.engine.trade_rules import IntradayTradeSettings

DAY = date(2026, 10, 21)
HALF = date(2025, 11, 28)
RTH = calendar.rth_bounds(DAY)
HALF_RTH = calendar.rth_bounds(HALF)
COSTS = IntradayCostModel()
CFG = ORBConfig(ORBSignal(), RankParams())
PCT = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None)
BASE = {"AAA": 100.0, "BBB": 50.0, "CCC": 40.0, "DDD": 30.0}
ET = "America/New_York"


def m(i: int, rth=RTH) -> pd.Timestamp:
    return rth[0] + pd.Timedelta(minutes=i)


def bar(o, h=None, lo=None, c=None, v=1000.0) -> dict:
    c = o if c is None else c
    return {"open": o, "high": max(o, c) if h is None else h,
            "low": min(o, c) if lo is None else lo, "close": c, "volume": v}


def tape(syms=("AAA",), over=None, missing=(), rth=RTH) -> dict:
    n = int((rth[1] - rth[0]) / pd.Timedelta(minutes=1))
    out = {}
    for i in range(n):
        out[m(i, rth)] = {s: bar(BASE[s]) for s in syms if (s, i) not in set(missing)}
    for (s, i), b in (over or {}).items():
        out[m(i, rth)][s] = b
    return out


def signals(spec: dict[str, tuple], score: float = 1.0, rth=RTH):
    """A stand-in strategy: ``score`` for symbol s at the listed minutes. The
    symbol is recognised by its base price on the first bar of the day."""
    by_base = {BASE[s]: {m(i, rth) for i in mins} for s, mins in spec.items()}

    def strategy(today, t, rth_open, atr, p):
        return score if t in by_base.get(float(today["close"].iloc[0]), set()) else None
    return strategy


def run(tp, *, trade=PCT, strategy=None, shortlist=("AAA",), watched=None, gating="ok",
        mode="live", cash=2000.0, adv=None, score_floor=None, rth=RTH, prior=None,
        upto=None, state=None, day=DAY):
    state = state or GroupDayState(day, cash, {}, [], {}, cash)
    ctx = DayContext.from_prior(prior or {})
    inputs = [(t, tp.get(t, {}), list(shortlist if watched is None else watched),
               gating(t) if callable(gating) else gating, mode(t) if callable(mode) else mode)
              for t in sorted(tp) if upto is None or t <= upto]
    events = run_day(state, ctx, inputs, strategy=strategy, strategy_cfg=CFG, trade=trade,
                     costs=COSTS, adv=adv or {}, score_floor=score_floor,
                     shortlist=list(shortlist), rth=rth, risk_pct=0.03)
    return state, events


def ev(events, kind=None, side=None, symbol=None):
    return [e for e in events if (kind is None or e.kind == kind)
            and (side is None or e.side == side) and (symbol is None or e.symbol == symbol)]


def et(ts: pd.Timestamp) -> str:
    return ts.tz_convert(ET).strftime("%H:%M")


# --- fills ------------------------------------------------------------------

def test_market_order_fills_at_the_next_bar_open():
    _, events = run(tape(over={("AAA", 21): bar(100.5)}), strategy=signals({"AAA": (20,)}))
    [order] = ev(events, "order", "buy")
    [fill] = ev(events, "fill", "buy")
    assert order.bar_ts == m(20) and fill.bar_ts == m(21)
    assert fill.raw_price == 100.5
    assert fill.eff_price == round(100.5 + 100.5 * 8 / 1e4, 6)
    assert order.ref == fill.ref == f"buy:AAA:{m(20):%Y%m%dT%H%MZ}:entry"


def test_limit_order_fills_when_touched_at_the_better_of_open_and_limit():
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, entry_limit_offset_pct=-0.001)
    _, events = run(tape(over={("AAA", 21): bar(100.2, lo=99.8, c=100.0)}), trade=trade,
                    strategy=signals({"AAA": (20,)}))
    [fill] = ev(events, "fill", "buy")
    assert fill.raw_price == 99.9 and fill.order_kind == "limit" and fill.bar_ts == m(21)


def test_limit_order_not_touched_keeps_waiting_then_expires_on_the_fourth_minute():
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, entry_limit_offset_pct=-0.01)
    state, events = run(tape(), trade=trade, strategy=signals({"AAA": (20,)}), upto=m(23))
    assert ev(events, "fill") == [] and len(state.pending) == 1
    _, events = run(tape(), trade=trade, strategy=signals({"AAA": (20,)}), upto=m(24))
    [exp] = ev(events, "expire")
    assert exp.bar_ts == m(24) and exp.reason == "ttl"


def test_an_order_decided_at_t_never_fills_at_t():
    state, events = run(tape(), strategy=signals({"AAA": (20,)}), upto=m(20))
    assert ev(events, "fill") == [] and [p.symbol for p in state.pending] == ["AAA"]


# --- resting exits ----------------------------------------------------------

TP = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, take_profit_pct=0.01)


def _exit(over, trade=TP, **kw):
    _, events = run(tape(over=over), trade=trade, strategy=signals({"AAA": (20,)}), **kw)
    return ev(events, "fill", "sell")


def test_same_bar_stop_and_target_takes_the_stop():
    [x] = _exit({("AAA", 25): bar(100.0, h=101.5, lo=98.5, c=100.0)})
    assert (x.raw_price, x.reason, x.order_kind, x.bar_ts) == (99.0, "hard_stop", "stop", m(25))


def test_gap_through_the_stop_fills_at_the_open():
    [x] = _exit({("AAA", 25): bar(98.0)})
    assert (x.raw_price, x.reason) == (98.0, "hard_stop")


def test_gap_through_the_target_fills_at_the_open():
    [x] = _exit({("AAA", 25): bar(102.0)})
    assert (x.raw_price, x.reason, x.order_kind) == (102.0, "take_profit", "target")


def test_target_touched_intrabar_fills_at_the_target():
    [x] = _exit({("AAA", 25): bar(100.0, h=101.2, c=100.5)})
    assert (x.raw_price, x.reason) == (101.0, "take_profit")


def test_entry_bar_can_hit_its_own_stop():
    [x] = _exit({("AAA", 21): bar(100.0, lo=98.9, c=99.5)})
    assert x.bar_ts == m(21) and x.raw_price == 99.0


def test_minute_without_a_bar_never_triggers_an_exit():
    _, events = run(tape(over={("AAA", 23): bar(100.0, lo=98.5)}, missing={("AAA", 22)}),
                    trade=TP, strategy=signals({"AAA": (20,)}))
    assert ev(events, symbol="AAA", kind="fill", side="sell")[0].bar_ts == m(23)
    assert [e for e in events if e.bar_ts == m(22)] == []


def test_max_hold_counts_wall_clock_minutes_across_missing_bars():
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, max_hold_minutes=5)
    _, events = run(tape(missing={("AAA", 22), ("AAA", 23), ("AAA", 24)}), trade=trade,
                    strategy=signals({"AAA": (20,)}))
    [order] = ev(events, "order", "sell")
    [x] = ev(events, "fill", "sell")
    assert order.bar_ts == m(25) and order.reason == "max_hold"
    assert x.bar_ts == m(26) and x.reason == "max_hold" and x.order_kind == "market"


# --- time exits ---------------------------------------------------------------

def test_flatten_is_decided_at_1554_and_filled_at_1555():
    _, events = run(tape(), strategy=signals({"AAA": (20,)}))
    [order] = ev(events, "order", "sell")
    [x] = ev(events, "fill", "sell")
    assert (et(order.bar_ts), et(x.bar_ts), x.reason) == ("15:54", "15:55", "flatten")


def test_half_day_flatten_is_decided_at_1254_and_filled_at_1255():
    _, events = run(tape(rth=HALF_RTH), strategy=signals({"AAA": (20,)}, rth=HALF_RTH),
                    rth=HALF_RTH, day=HALF)
    [order] = ev(events, "order", "sell")
    [x] = ev(events, "fill", "sell")
    assert (et(order.bar_ts), et(x.bar_ts)) == ("12:54", "12:55")


def test_last_minute_closes_at_the_last_close_and_cancels_the_unfilled_flatten():
    missing = {("AAA", i) for i in range(370, 390)}
    _, events = run(tape(over={("AAA", 369): bar(101.0)}, missing=missing),
                    strategy=signals({"AAA": (20,)}))
    [x] = ev(events, "fill", "sell")
    assert (et(x.bar_ts), x.raw_price, x.reason) == ("15:59", 101.0, "flatten_last_close")
    [c] = ev(events, "cancel", "sell")
    assert c.reason == "lot closed" and c.ref.endswith(":flatten")


def test_pending_max_hold_sell_is_cancelled_when_the_lot_closes():
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, max_hold_minutes=3)
    missing = {("AAA", i) for i in range(24, 390)}
    state, events = run(tape(missing=missing), trade=trade, strategy=signals({"AAA": (20,)}))
    [order] = ev(events, "order", "sell")
    assert order.reason == "max_hold" and order.bar_ts == m(23)
    [c] = ev(events, "cancel", "sell")
    assert c.ref == order.ref and c.reason == "lot closed"
    assert state.pending == [] and state.lots == {}


# --- gating / modes -----------------------------------------------------------

def test_catchup_never_enters_but_still_exits():
    tp = tape(("AAA", "BBB"), over={("AAA", 40): bar(98.0)})
    _, events = run(tp, strategy=signals({"AAA": (20,), "BBB": (30,)}),
                    shortlist=("AAA", "BBB"), mode=lambda t: "live" if t < m(25) else "catchup")
    assert ev(events, "order", "buy", "BBB") == []
    [x] = ev(events, "fill", "sell", "AAA")
    assert x.bar_ts == m(40) and x.reason == "hard_stop"


@pytest.mark.parametrize("gate, reason", [("blocked: HALT", "blocked: HALT"),
                                          ("paused", "blocked: paused"),
                                          ("refused", "blocked: refused")])
def test_blocked_gate_cancels_buys_but_exits_still_run(gate, reason):
    tp = tape(("AAA", "BBB"), over={("AAA", 30): bar(98.0)})
    _, events = run(tp, strategy=signals({"AAA": (5,), "BBB": (20,)}), shortlist=("AAA", "BBB"),
                    gating=lambda t: "ok" if t <= m(20) else gate)
    [c] = ev(events, "cancel", "buy", "BBB")
    assert c.reason == reason and c.bar_ts == m(21)
    assert ev(events, "fill", "buy", "BBB") == []
    [x] = ev(events, "fill", "sell", "AAA")
    assert x.bar_ts == m(30)


def test_intraday_loss_pause_cancels_pending_buys_and_stops_entries():
    trade = IntradayTradeSettings(stop_pct=0.10, stop_atr_mult=None)
    tp = tape(("AAA", "BBB", "CCC"),
              over={("AAA", i): bar(93.0) for i in range(31, 390)},
              missing={("BBB", 31), ("BBB", 32)})
    state, events = run(tp, trade=trade, strategy=signals({"AAA": (20,), "BBB": (30,),
                                                            "CCC": (35,)}),
                        shortlist=("AAA", "BBB", "CCC"))
    assert state.entries_paused_reason == "intraday_loss_pause"
    [c] = ev(events, "cancel", "buy", "BBB")
    assert c.reason == "blocked: intraday_loss_pause" and c.bar_ts == m(31)
    assert ev(events, "order", "buy", "CCC") == []


# --- slots / limits -----------------------------------------------------------

def test_concurrent_slot_cap_follows_the_ladder():
    syms = ("AAA", "BBB", "CCC", "DDD")
    _, events = run(tape(syms), strategy=signals({s: (20,) for s in syms}), shortlist=syms)
    assert sizing.concurrent_slots(2000.0) == 3
    assert [e.symbol for e in ev(events, "order", "buy")] == ["AAA", "BBB", "CCC"]


@pytest.mark.parametrize("cap, again", [(1, False), (2, True)])
def test_max_entries_per_symbol_day(cap, again):
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None,
                                  max_entries_per_symbol_day=cap)
    _, events = run(tape(over={("AAA", 25): bar(98.0)}), trade=trade,
                    strategy=signals({"AAA": (20, 30)}))
    assert bool([e for e in ev(events, "order", "buy") if e.bar_ts == m(30)]) is again


def test_insufficient_cash_cancels_the_buy():
    _, events = run(tape(), strategy=signals({"AAA": (20,)}), cash=0.5)
    [c] = ev(events, "cancel", "buy")
    assert c.reason == "insufficient cash" and ev(events, "fill") == []


def test_cash_rounds_to_cents_half_up_once_a_minute():
    state = GroupDayState(DAY, 1000.005, {}, [], {}, 1000.005)
    run(tape(), state=state, upto=m(0))
    assert state.cash == 1000.01
    state = GroupDayState(DAY, 1000.004999, {}, [], {}, 1000.0)
    run(tape(), state=state, upto=m(0))
    assert state.cash == 1000.0


def test_qty_is_the_sized_lot_truncated_to_six_places():
    import math

    state, events = run(tape(), strategy=signals({"AAA": (20,)}), upto=m(21))
    [fill] = ev(events, "fill", "buy")
    eff = COSTS.entry_fill(100.0)
    want = math.floor(sizing.position_size(2000.0, eff, 99.0, risk_pct=0.03, slots=3,
                                           settled_cash=2000.0) * 1e6) / 1e6
    assert fill.qty == want == state.lots["AAA"].shares
    assert fill.qty != sizing.position_size(2000.0, eff, 99.0, risk_pct=0.03, slots=3,
                                            settled_cash=2000.0)
    assert state.cash == round(2000.0 - want * eff, 2)


def test_score_floor_gates_entries():
    strat = signals({"AAA": (20,)}, score=1.0)
    assert ev(run(tape(), strategy=strat, score_floor=2.0)[1], "order", "buy") == []
    assert len(ev(run(tape(), strategy=strat, score_floor=0.5)[1], "order", "buy")) == 1


def test_symbols_outside_watched_never_enter():
    _, events = run(tape(("AAA", "BBB")), strategy=signals({"AAA": (20,), "BBB": (20,)}),
                    shortlist=("AAA", "BBB"), watched=("AAA",))
    assert [e.symbol for e in ev(events, "order", "buy")] == ["AAA"]


def test_orders_follow_the_shortlist_order():
    syms = ("CCC", "AAA", "BBB")
    _, events = run(tape(syms), strategy=signals({s: (20,) for s in syms}), shortlist=syms)
    assert [e.symbol for e in ev(events, "order", "buy")] == ["CCC", "AAA", "BBB"]


def test_entries_today_counts_fills_not_orders():
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, entry_limit_offset_pct=-0.01)
    state, events = run(tape(), trade=trade, strategy=signals({"AAA": (20, 30)}), upto=m(29))
    assert state.entries_today == {} and len(ev(events, "expire")) == 1
    _, events = run(tape(), trade=trade, strategy=signals({"AAA": (20, 30)}), upto=m(30))
    assert [e.bar_ts for e in ev(events, "order", "buy")] == [m(20), m(30)]


def test_limit_price_rounds_to_four_places():
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, entry_limit_offset_pct=-0.001)
    state, _ = run(tape(over={("AAA", 20): bar(100.0, c=100.12345)}), trade=trade,
                   strategy=signals({"AAA": (20,)}), upto=m(20))
    assert state.pending[0].limit_price == round(100.12345 * 0.999, 4)


# --- stop / target distances and the fill record ------------------------------

def _prior_frame(day: date, px: float = 100.0) -> pd.DataFrame:
    o, c = calendar.rth_bounds(day)
    ts = pd.date_range(o, c, freq="1min", inclusive="left")
    k = pd.Series(range(len(ts)), dtype="float64")
    return pd.DataFrame({"ts": ts, "open": px, "high": px + 0.3 + (k % 7) * 0.05,
                         "low": px - 0.2 - (k % 5) * 0.04, "close": px + (k % 3) * 0.05,
                         "volume": 1000.0, "vwap": px, "trade_count": 5.0})


def test_stop_distance_from_stop_pct_or_from_the_atr():
    state_pct = run(tape(over={("AAA", 20): bar(100.0, c=100.5)}),
                    strategy=signals({"AAA": (20,)}), upto=m(21))[0]
    assert state_pct.lots["AAA"].stop == round(100.0 - 0.01 * 100.5, 6)

    prior = {"AAA": pd.concat([_prior_frame(date(2026, 10, 19)),
                               _prior_frame(date(2026, 10, 20))], ignore_index=True)}
    tp = tape()
    state, _ = run(tp, trade=IntradayTradeSettings(), strategy=signals({"AAA": (20,)}),
                   prior=prior, upto=m(21))
    today = pd.DataFrame([{"ts": t, **tp[t]["AAA"]} for t in sorted(tp) if t <= m(20)])
    hist = pd.concat([prior["AAA"][list(today.columns)], today], ignore_index=True)
    atr = atr_window(hist, 14)
    assert atr is not None and atr > 0
    assert state.lots["AAA"].stop == round(100.0 - 1.0 * atr, 6)


def test_buy_fill_carries_the_lot_record():
    state, events = run(tape(), trade=TP, strategy=signals({"AAA": (20,)}, score=2.5), upto=m(21))
    [fill] = ev(events, "fill", "buy")
    lot = state.lots["AAA"]
    assert fill.eff_price == lot.avg_cost == round(COSTS.entry_fill(100.0), 6)
    assert fill.qty == lot.shares and fill.r_unit == lot.r_unit and fill.score == 2.5
    assert lot.stop == 99.0 and lot.target == 101.0
    assert lot.r_unit == round(COSTS.entry_fill(100.0) - 99.0, 6)
    assert et(lot.flatten_decide_ts) == "15:54" and lot.fill_bar_ts == m(21)


# --- determinism / no lookahead with the real ORB score -----------------------

def _orb_tape():
    over = {("AAA", i): bar(100.0, h=100.5, c=100.0) for i in range(15)}
    over[("AAA", 20)] = bar(100.4, h=101.2, c=101.0, v=3000.0)
    over[("AAA", 50)] = bar(101.0, h=101.0, lo=98.0, c=98.5)
    return tape(over=over)


def _prior():
    return {"AAA": pd.concat([_prior_frame(date(2026, 10, 19)), _prior_frame(date(2026, 10, 20))],
                             ignore_index=True)}


def test_same_input_gives_identical_events():
    a = run(_orb_tape(), trade=IntradayTradeSettings(), strategy=orb_score, prior=_prior())[1]
    b = run(_orb_tape(), trade=IntradayTradeSettings(), strategy=orb_score, prior=_prior())[1]
    assert a == b and len(ev(a, "fill", "buy")) == 1


def test_bars_after_t_never_change_earlier_events():
    full = run(_orb_tape(), trade=IntradayTradeSettings(), strategy=orb_score, prior=_prior())[1]
    tp = _orb_tape()
    for t in [t for t in tp if t > m(30)]:
        tp[t]["AAA"] = bar(150.0)
    other = run(tp, trade=IntradayTradeSettings(), strategy=orb_score, prior=_prior())[1]
    cut = m(30)
    assert [e for e in full if e.bar_ts <= cut] == [e for e in other if e.bar_ts <= cut]
    assert [e for e in full if e.bar_ts <= cut] != []


# --- the context --------------------------------------------------------------

def test_context_minutes_must_strictly_increase():
    ctx = DayContext.from_prior({})
    ctx.add_minute(m(1), {"AAA": bar(100.0)})
    with pytest.raises(ValueError):
        ctx.add_minute(m(1), {})
    with pytest.raises(ValueError):
        ctx.add_minute(m(0), {})


def test_context_bar_today_and_history():
    prior = {"AAA": _prior_frame(date(2026, 10, 20))}
    ctx = DayContext.from_prior(prior)
    ctx.add_minute(m(0), {"AAA": bar(100.0)})
    ctx.add_minute(m(2), {"AAA": bar(101.0)})
    assert ctx.bar("AAA", m(1)) is None and ctx.bar("AAA", m(2))["close"] == 101.0
    assert list(ctx.today("AAA")["ts"]) == [m(0), m(2)]
    h = ctx.history("AAA")
    assert len(h) == 390 + 2 and h["ts"].is_monotonic_increasing


def test_step_without_a_strategy_only_manages_exits():
    state = GroupDayState(DAY, 2000.0, {}, [], {}, 2000.0)
    ctx = DayContext.from_prior({})
    ctx.add_minute(m(20), {"AAA": bar(100.0)})
    out = step(state, ctx, m(20), strategy=None, strategy_cfg=None, trade=PCT, costs=COSTS,
               adv={}, gating="ok", score_floor=None, watched=["AAA"], shortlist=["AAA"],
               rth=RTH, risk_pct=0.03, mode="live")
    assert out == [] and state.pending == []


# --- revision A: inside-bar limit fills and the boundaries ---------------------

LIMIT_TP = dict(take_profit_pct=0.005, entry_limit_offset_pct=-0.002, stop_atr_mult=None)


def test_limit_filled_inside_the_bar_skips_open_and_target_exits_on_that_bar():
    trade = IntradayTradeSettings(stop_pct=0.03, **LIMIT_TP)
    tp = tape(over={("AAA", 21): bar(100.3, h=101.0, lo=98.0, c=99.0),
                    ("AAA", 22): bar(100.5)})
    state, events = run(tp, trade=trade, strategy=signals({"AAA": (20,)}), upto=m(21))
    [fill] = ev(events, "fill", "buy")
    assert fill.raw_price == 99.8 and fill.bar_ts == m(21)
    assert ev(events, "fill", "sell") == [] and "AAA" in state.lots
    assert state.lots["AAA"].target == 100.3 and state.lots["AAA"].stop == 96.8
    _, events = run(tp, trade=trade, strategy=signals({"AAA": (20,)}), upto=m(22))
    [x] = ev(events, "fill", "sell")
    assert (x.bar_ts, x.raw_price, x.reason) == (m(22), 100.5, "take_profit")


def test_limit_filled_inside_the_bar_still_takes_its_stop_on_that_bar():
    trade = IntradayTradeSettings(stop_pct=0.01, **LIMIT_TP)
    tp = tape(over={("AAA", 21): bar(100.3, h=101.0, lo=98.0, c=99.0)})
    _, events = run(tp, trade=trade, strategy=signals({"AAA": (20,)}), upto=m(21))
    [x] = ev(events, "fill", "sell")
    assert (x.bar_ts, x.reason, x.order_kind) == (m(21), "hard_stop", "stop")
    assert x.raw_price == round(99.8 - 0.01 * 100.0, 6)


def test_limit_filled_at_the_open_keeps_the_target_rule():
    trade = IntradayTradeSettings(stop_pct=0.01, take_profit_pct=0.001,
                                  entry_limit_offset_pct=0.01, stop_atr_mult=None)
    _, events = run(tape(over={("AAA", 21): bar(100.2, h=100.3)}), trade=trade,
                    strategy=signals({"AAA": (20,)}), upto=m(21))
    [fill] = ev(events, "fill", "buy")
    [x] = ev(events, "fill", "sell")
    assert fill.raw_price == 100.2 and (x.raw_price, x.reason) == (100.3, "take_profit")


def test_limit_order_gapping_below_the_limit_fills_at_the_open():
    trade = IntradayTradeSettings(stop_pct=0.05, stop_atr_mult=None, entry_limit_offset_pct=-0.002)
    _, events = run(tape(over={("AAA", 21): bar(99.0, c=99.5)}), trade=trade,
                    strategy=signals({"AAA": (20,)}), upto=m(21))
    [fill] = ev(events, "fill", "buy")
    assert fill.raw_price == 99.0


def test_context_atr_reads_only_todays_tail_once_it_is_long_enough():
    ctx = DayContext.from_prior({"AAA": _prior_frame(date(2026, 10, 20), px=50.0)})
    rows = []
    for i in range(70):
        b = bar(100.0 + (i % 4) * 0.1, h=100.6 + (i % 3) * 0.2, lo=99.5 - (i % 5) * 0.1,
                c=100.0 + (i % 6) * 0.05)
        ctx.add_minute(m(i), {"AAA": b})
        rows.append({"ts": m(i), **b})
    today = pd.DataFrame(rows)
    for i in (55, 56, 69):
        assert ctx.atr("AAA", m(i), 14) == atr_window(today[today["ts"] <= m(i)], 14)
    mixed = pd.concat([_prior_frame(date(2026, 10, 20), px=50.0)[list(today.columns)],
                       today[today["ts"] <= m(30)]], ignore_index=True)
    assert ctx.atr("AAA", m(30), 14) == atr_window(mixed, 14)


def test_sells_fill_before_buys_decided_on_the_same_bar():
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, max_hold_minutes=3)
    _, events = run(tape(("AAA", "BBB")), trade=trade, shortlist=("AAA", "BBB"),
                    strategy=signals({"AAA": (20,), "BBB": (23,)}), upto=m(24))
    fills = [(e.side, e.symbol) for e in ev(events, "fill") if e.bar_ts == m(24)]
    assert fills == [("sell", "AAA"), ("buy", "BBB")]


@pytest.mark.parametrize("minute, orders", [(119, 1), (120, 0)])
def test_entry_window_cutoff_is_exclusive(minute, orders):
    _, events = run(tape(), strategy=signals({"AAA": (minute,)}), upto=m(minute))
    assert len(ev(events, "order", "buy")) == orders


def test_flatten_minus_ttl_cutoff_is_exclusive():
    cfg_wide = ORBConfig(ORBSignal(entry_window_minutes=390), RankParams())
    for minute, orders in ((380, 1), (381, 0)):
        state = GroupDayState(DAY, 2000.0, {}, [], {}, 2000.0)
        tp = tape()
        inputs = [(t, tp[t], ["AAA"], "ok", "live") for t in sorted(tp) if t <= m(minute)]
        events = run_day(state, DayContext.from_prior({}), inputs,
                         strategy=signals({"AAA": (minute,)}), strategy_cfg=cfg_wide,
                         trade=PCT, costs=COSTS, adv={}, score_floor=None, shortlist=["AAA"],
                         rth=RTH, risk_pct=0.03)
        assert len(ev(events, "order", "buy")) == orders, minute


def test_fill_slots_come_from_the_decision_equity_not_the_live_one():
    syms = ("AAA", "BBB", "CCC", "DDD")
    tp = tape(syms, over={("BBB", 31): bar(60.0)})
    state, events = run(tp, strategy=signals({"AAA": (20,), "CCC": (20,), "BBB": (30,),
                                             "DDD": (30,)}),
                        shortlist=syms, cash=5100.0, upto=m(31))
    assert [p for p in events if p.kind == "order" and p.bar_ts == m(30)] != []
    assert sizing.concurrent_slots(5050.0) == 4 and sizing.concurrent_slots(4900.0) == 3
    assert [e.symbol for e in ev(events, "fill", "buy") if e.bar_ts == m(31)] == ["BBB", "DDD"]
    assert ev(events, "cancel") == []


def test_cash_is_rounded_once_per_minute_not_per_fill():
    from quant.backtest.intraday import _cents

    state, events = run(tape(("AAA", "BBB")), strategy=signals({"AAA": (20,), "BBB": (20,)}),
                        shortlist=("AAA", "BBB"), upto=m(21))
    a, b = ev(events, "fill", "buy")
    ea, eb = COSTS.entry_fill(a.raw_price), COSTS.entry_fill(b.raw_price)
    once = _cents(round(round(2000.0 - a.qty * ea, 6) - b.qty * eb, 6))
    assert state.cash == once == 666.67
    assert once != _cents(_cents(2000.0 - a.qty * ea) - b.qty * eb)


@pytest.mark.parametrize("cash, paused", [(1750.0, True), (1750.01, False)])
def test_intraday_loss_pause_triggers_at_the_exact_threshold(cash, paused):
    trade = IntradayTradeSettings(stop_pct=0.01, stop_atr_mult=None, intraday_loss_pause_pct=0.125)
    state = GroupDayState(DAY, cash, {}, [], {}, 2000.0)
    run(tape(), trade=trade, state=state, upto=m(0))
    assert (state.entries_paused_reason == "intraday_loss_pause") is paused


# --- revision B: truncated qty under the cash cap, non-positive stop distance --

CASH_CAP_CASES = [0, 1, 5, 6, 8, 10, 12, 14, 15, 17, 18, 21, 22, 26, 27, 30, 31, 33, 34, 36,
                  39, 42, 45, 46]


@pytest.mark.parametrize("k", CASH_CAP_CASES)
def test_cash_capped_entry_fills_within_cash_when_rounding_would_go_up(k):
    from quant.backtest.intraday import OpenLot, Pending

    t0, t1 = m(29), m(30)
    cash, raw = 600.00 + k * 0.37, 37.13 + k * 0.011
    equity_ref = round(cash + 7.0 * 200.0, 2)
    eff = COSTS.entry_fill(raw)
    sized = sizing.position_size(equity_ref, eff, raw - 0.5, risk_pct=0.03,
                                 slots=sizing.concurrent_slots(equity_ref), settled_cash=cash)
    assert sized == cash / eff and round(sized, 6) * eff > cash + 1e-9
    lots = {"AAA": OpenLot("AAA", 7.0, 100.0, 99.0, None, t0, 1.0, 100.0, 3, None,
                           RTH[1] - pd.Timedelta(minutes=6))}
    p = Pending("buy:CCC:x:entry", "CCC", "buy", "market", "entry", t0,
                t0 + pd.Timedelta(minutes=3), None, 1.0, equity_ref, 0.5, None, None)
    state = GroupDayState(DAY, cash, lots, [p], {}, cash + 1400.0, None, {"AAA": 200.0})
    ctx = DayContext.from_prior({})
    ctx.add_minute(t1, {"CCC": bar(raw, h=raw + 0.1, lo=raw - 0.05), "AAA": bar(200.0)})
    events = step(state, ctx, t1, strategy=None, strategy_cfg=CFG, trade=IntradayTradeSettings(),
                  costs=COSTS, adv={}, gating="ok", score_floor=None, watched=["AAA", "CCC"],
                  shortlist=["AAA", "CCC"], rth=RTH, risk_pct=0.03, mode="live")
    assert ev(events, "cancel") == []
    [fill] = ev(events, "fill", "buy", "CCC")
    assert fill.qty * eff <= cash and cash - fill.qty * eff < 1e-6 * eff
    assert state.cash >= 0


def test_zero_atr_stop_distance_places_no_order():
    flat = _prior_frame(date(2026, 10, 20))
    flat[["open", "high", "low", "close"]] = 100.0
    prior = {"AAA": flat}
    _, events = run(tape(), trade=IntradayTradeSettings(), strategy=signals({"AAA": (20,)}),
                    prior=prior, upto=m(21))
    assert ev(events, "order") == [] and ev(events, "fill") == []
    _, events = run(tape(), trade=PCT, strategy=signals({"AAA": (20,)}), prior=prior, upto=m(21))
    assert len(ev(events, "fill", "buy")) == 1
