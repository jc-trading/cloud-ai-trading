"""P3c — a group's trade settings mean the same thing in the simulator and live.

Entries: the open limit (``entry_limit_offset_pct``) on the open_once entry step,
same bars on both sides as test_phase_c_entry_parity. Exits: take-profit,
holding style and max hold on the post-close exit pass — the simulator's
decision (date, reason, raw price) against ``daily_exit_management`` replayed
night by night over the same bars. Prices are compared BEFORE costs: live
closes without an adv, so its cost-inclusive fills differ by design.

Offline: bars, the ledger session and the position store are stubbed.
"""

import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pandas as pd
import pytest

import app.models_registry  # noqa: F401

from app.modules.simledger import cycles
from app.modules.simledger.models import (AccountSnapshot, Recommendation,
                                          SimAccount, SimOrder, SimPosition)
from app.modules.simledger.service import SimLedgerService, _dec
from quant.backtest import simulator
from quant.engine import funnel as qfunnel
from quant.engine import trade_rules
from quant.engine.exits import ExitParams, Position
from quant.engine.trade_rules import TradeSettings

D1 = date(2026, 8, 17)
D2 = date(2026, 8, 18)
NOW = datetime(2026, 8, 18, 13, 50, tzinfo=timezone.utc)
CAPITAL = 2000.0
PARITY_FUNNEL = qfunnel.FunnelParams(min_confidence=0.0, atr_pct_min=0.0)
SECTORS = {"AAA": "tech", "BBB": "energy", "CCC": "health", "SPY": "etf"}
LEDGER_DP = 1e-6
EP = ExitParams()


def _frame(closes, *, last_day=D2, vol=50_000_000):
    n = len(closes)
    ts = pd.DatetimeIndex([pd.Timestamp(last_day, tz="America/New_York")
                           - pd.Timedelta(days=n - 1 - i)
                           for i in range(n)]).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts,
        "open": [c * 0.995 for c in closes],
        "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes],
        "close": closes,
        "volume": [vol] * n, "vwap": closes, "trade_count": [100] * n,
    })


def _bars():
    return {
        "AAA": _frame([50 + i * 0.80 for i in range(130)]),
        "BBB": _frame([40 + i * 0.55 for i in range(130)]),
        "CCC": _frame([30 + i * 0.35 for i in range(130)]),
        "SPY": _frame([100.0] * 130),
    }


class _Scalars(list):
    def all(self):
        return list(self)


class _Result:
    def __init__(self, rows=(), one=None, all_rows=()):
        self._rows, self._one, self._all = list(rows), one, list(all_rows)

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._one

    def all(self):
        return self._all


class _Session:
    def __init__(self, recs, snapshot, sell_fills=()):
        self._recs, self._snapshot = recs, snapshot
        self.sell_fills = list(sell_fills)
        self.added = []

    async def execute(self, stmt):
        cols = stmt.column_descriptions
        entity = cols[0].get("entity") if cols else None
        if entity is Recommendation:
            return _Result(rows=self._recs)
        if entity is AccountSnapshot:
            return _Result(one=self._snapshot)
        if entity is SimOrder:
            sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return _Result(one=None,
                           all_rows=self.sell_fills if "'sell'" in sql else [])
        if entity is SimPosition:
            return _Result(one=None)
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


# --- entries ------------------------------------------------------------------

def _simulator_entries(frames, monkeypatch, trade=None):
    created = []

    class _Recorded(Position):
        def __init__(self, **kw):
            created.append(dict(kw))
            super().__init__(**kw)

    monkeypatch.setattr(simulator.barsmod, "get_bars",
                        lambda symbol, tf="1d", **kw: frames[symbol])
    monkeypatch.setattr(simulator, "Position", _Recorded)
    cfg = simulator.SimConfig(start=str(D1), end=str(D2),
                              starting_capital=CAPITAL, funnel=PARITY_FUNNEL)
    simulator.run(["AAA", "BBB", "CCC"], SECTORS, cfg, trade=trade)
    return created


def _live_entries(frames, monkeypatch, trade=None):
    at_d1 = {s: f.iloc[:-1].reset_index(drop=True) for s, f in frames.items()}
    batch = cycles.build_recommendations(
        ["AAA", "BBB", "CCC"], D1, funnel_params=PARITY_FUNNEL,
        bars_fn=lambda s, tf, end: at_d1[s], sectors=SECTORS)
    recs = sorted([Recommendation(id=uuid4(), **r) for r in batch.rows
                   if r["shortlist_rank"] is not None], key=lambda r: r.shortlist_rank)
    assert recs
    account = SimAccount(id=uuid4(), user_id=uuid4(), name="system", is_system=True,
                         starting_capital=_dec(CAPITAL), cash=_dec(CAPITAL))
    snapshot = AccountSnapshot(id=uuid4(), account_id=account.id, snapshot_date=D1,
                               equity=_dec(CAPITAL), cash=_dec(CAPITAL), open_positions=0)
    session = _Session(recs, snapshot)

    async def _none(db, account_id):
        return []

    async def _no_closes(db, account_id, session_date):
        return []

    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(_none))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(_no_closes))
    open_prices = {s: float(f["open"].iloc[-1]) for s, f in frames.items()}
    booked = asyncio.run(cycles.run_entries(
        session, account, D2, group_id=uuid4(), now=NOW,
        entry_mode=cycles.ENTRY_MODE_OPEN_ONCE, open_prices=open_prices,
        trade_settings=trade))
    return booked, [o for o in session.added if isinstance(o, SimPosition)], session


def _rows_sim(created):
    return sorted(((p["symbol"], float(p["shares"]), float(p["avg_cost"]), float(p["stop"]))
                   for p in created), key=lambda r: r[0])


def _rows_live(lots):
    return sorted(((p.symbol, float(p.shares), float(p.avg_cost), float(p.stop))
                   for p in lots), key=lambda r: r[0])


def _assert_same_entries(sim, live):
    assert [r[0] for r in sim] == [r[0] for r in live]
    for got, want in zip(live, sim):
        for g, w in zip(got[1:], want[1:]):
            assert g == pytest.approx(w, abs=LEDGER_DP)


def test_e0_guard_both_sides_enter_aaa_without_settings(monkeypatch):
    frames = _bars()
    sim = _rows_sim(_simulator_entries(frames, monkeypatch))
    booked, lots, _ = _live_entries(frames, monkeypatch)
    assert "AAA" in [r[0] for r in sim] and "AAA" in booked
    _assert_same_entries(sim, _rows_live(lots))


def test_e1_zero_offset_blocks_aaa_on_both_sides(monkeypatch):
    frames = _bars()
    aaa = frames["AAA"]
    assert float(aaa["open"].iloc[-1]) > float(aaa["close"].iloc[-2])
    ts = TradeSettings(entry_limit_offset_pct=0.0)
    sim = _rows_sim(_simulator_entries(frames, monkeypatch, ts))
    booked, lots, _ = _live_entries(frames, monkeypatch, ts)
    assert "AAA" not in [r[0] for r in sim] and "AAA" not in booked
    assert sim
    _assert_same_entries(sim, _rows_live(lots))


def test_e2_wide_offset_equals_no_settings(monkeypatch):
    frames = _bars()
    base_sim = _rows_sim(_simulator_entries(frames, monkeypatch))
    base_live = _rows_live(_live_entries(frames, monkeypatch)[1])
    ts = TradeSettings(entry_limit_offset_pct=0.03)
    assert _rows_sim(_simulator_entries(frames, monkeypatch, ts)) == base_sim
    assert _rows_live(_live_entries(frames, monkeypatch, ts)[1]) == base_live


def test_e3_deep_offset_enters_nothing(monkeypatch):
    frames = _bars()
    ts = TradeSettings(entry_limit_offset_pct=-0.10)
    assert _simulator_entries(frames, monkeypatch, ts) == []
    booked, lots, session = _live_entries(frames, monkeypatch, ts)
    assert booked == [] and lots == []
    assert not any(isinstance(o, SimPosition) for o in session.added)


# --- exits --------------------------------------------------------------------

def _day(i_from_d2: int) -> date:
    return date.fromordinal(D2.toordinal() + i_from_d2)


def _exit_frames(post, *, history=None):
    """AAA: ``history`` closes (ramp by default) through D2, then ``post`` explicit
    (open, high, low, close) bars on the following days; SPY flat throughout."""
    closes = history or [50 + i * 0.80 for i in range(130)]
    base = _frame(closes, last_day=D2)
    if post:
        extra = pd.DataFrame({
            "ts": pd.DatetimeIndex([pd.Timestamp(_day(k + 1), tz="America/New_York")
                                    for k in range(len(post))]).tz_convert("UTC"),
            "open": [b[0] for b in post], "high": [b[1] for b in post],
            "low": [b[2] for b in post], "close": [b[3] for b in post],
            "volume": [50_000_000] * len(post), "vwap": [b[3] for b in post],
            "trade_count": [100] * len(post)})
        base = pd.concat([base, extra], ignore_index=True)
    spy = _frame([100.0] * len(base), last_day=_day(len(post)))
    return {"AAA": base, "SPY": spy}, _day(len(post))


def _parity_run(frames, last_day, trade, monkeypatch):
    real = trade_rules.decide_exit
    decisions = []

    def spy(pos, *a, **kw):
        d = real(pos, *a, **kw)
        if d is not None:
            decisions.append(d)
        return d

    monkeypatch.setattr(trade_rules, "decide_exit", spy)
    monkeypatch.setattr(simulator.sizing, "pyramid_allowed", lambda *a, **k: False)
    created = []

    class _Recorded(Position):
        def __init__(self, **kw):
            created.append(dict(kw))
            super().__init__(**kw)

    monkeypatch.setattr(simulator.barsmod, "get_bars",
                        lambda symbol, tf="1d", **kw: frames[symbol])
    monkeypatch.setattr(simulator, "Position", _Recorded)
    cfg = simulator.SimConfig(start=str(D1), end=str(last_day), starting_capital=CAPITAL,
                              funnel=PARITY_FUNNEL, exits=EP)
    res = simulator.run(["AAA"], SECTORS, cfg, trade=trade)
    assert created and created[0]["entry_date"] == D2
    t = [x for x in res.trades if x.exit_reason != "data_end"]
    sim = None
    if t:
        assert decisions
        sim = (t[0].exit_date, t[0].exit_reason, decisions[0].price)
    decisions.clear()

    kw = created[0]
    acct = SimAccount(id=uuid4(), user_id=uuid4(), name="grp", is_system=True,
                      starting_capital=_dec(CAPITAL), cash=_dec(CAPITAL))
    sp = SimPosition(id=uuid4(), account_id=acct.id, symbol="AAA", status="open",
                     shares=_dec(kw["shares"]), avg_cost=_dec(kw["avg_cost"]),
                     stop=_dec(kw["stop"]), r_unit=_dec(kw["r_unit"]),
                     high_water=_dec(kw["high_water"]), entry_date=kw["entry_date"],
                     adds_done=0, reversal_count=0, bars_held=0)
    live = []

    async def _open(db, account_id):
        return [sp] if sp.status == "open" else []

    async def _close(db, account, pos, *, raw_price, reason, idempotency_key):
        live.append((session, reason, raw_price))
        pos.status = "closed"

    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(_open))
    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(_close))
    session = None
    for d in sorted(set(res.equity.index)):
        if d < D2:
            continue
        session = d
        cut = {s: f[f["ts"].dt.tz_convert("America/New_York").dt.date <= d]
               .reset_index(drop=True) for s, f in frames.items()}
        asyncio.run(cycles.daily_exit_management(
            None, acct, d, exit_params=EP,
            bars_fn=lambda s, tf="1d", end=None, **k: cut[s], trade_settings=trade))
        if sp.status == "closed":
            break
    return sim, (live[0] if live else None), kw


def _assert_parity(sim, live):
    assert (sim is None) == (live is None)
    if sim is not None:
        assert sim[0] == live[0] and sim[1] == live[1]
        assert live[2] == pytest.approx(sim[2], abs=1e-5)


def _entry_open() -> float:
    return float(_frame([50 + i * 0.80 for i in range(130)])["open"].iloc[-1])


def test_x1_take_profit_inside_the_bar(monkeypatch):
    o = _entry_open()
    frames, last = _exit_frames([(o * 1.01, o * 1.08, o * 1.00, o * 1.02)])
    ts = TradeSettings(take_profit_pct=0.05)
    sim, live, kw = _parity_run(frames, last, ts, monkeypatch)
    target = kw["avg_cost"] * 1.05
    assert sim[1] == "take_profit" and sim[2] == pytest.approx(target)
    assert sim[2] > o * 1.01
    _assert_parity(sim, live)


def test_x2_take_profit_gap_fills_at_the_open(monkeypatch):
    o = _entry_open()
    frames, last = _exit_frames([(o * 1.07, o * 1.09, o * 1.06, o * 1.08)])
    ts = TradeSettings(take_profit_pct=0.05)
    sim, live, kw = _parity_run(frames, last, ts, monkeypatch)
    assert sim[1] == "take_profit" and sim[2] == pytest.approx(o * 1.07)
    assert sim[2] >= kw["avg_cost"] * 1.05
    _assert_parity(sim, live)


def test_x3_entry_day_high_above_target_waits_for_the_next_session(monkeypatch):
    o = _entry_open()
    frames, last = _exit_frames([(o * 1.01, o * 1.08, o * 1.0, o * 1.02),
                                 (o * 1.005, o * 1.01, o * 1.0, o * 1.008)])
    frames["AAA"].loc[129, "high"] = o * 1.08
    ts = TradeSettings(take_profit_pct=0.05)
    sim, live, kw = _parity_run(frames, last, ts, monkeypatch)
    assert kw["avg_cost"] * 1.05 < o * 1.08
    assert sim is not None and live is not None
    assert sim[0] == live[0] == _day(1)
    assert sim[1] == live[1] == "take_profit"
    _assert_parity(sim, live)


def test_x4_max_hold_exits_at_the_close_on_the_same_day(monkeypatch):
    frames, last = _exit_frames(
        [(c * 0.995, c * 1.001, c * 0.999, c) for c in (153.6, 154.0, 154.4, 154.8, 155.2)])
    ts = TradeSettings(max_hold_days=3)
    sim, live, _ = _parity_run(frames, last, ts, monkeypatch)
    assert sim == (_day(3), "max_hold", 154.4)
    _assert_parity(sim, live)


def _reversal_history():
    ramp = [50 + i * 0.5 for i in range(90)]
    plateau = [ramp[-1] + (0.6 if i % 2 else -0.6) for i in range(38)]
    return ramp + plateau + [ramp[-1] + 1.5, ramp[-1] + 2.0]


def _reversal_frames():
    h = _reversal_history()
    top = h[-1]
    post = [(c * 1.0, c * 1.02, c * 0.98, c) for c in
            [top - 1.5 - 0.3 * k for k in range(12)]]
    frames, last = _exit_frames(post, history=h)
    aaa = frames["AAA"]
    n = len(h)
    aaa.loc[:n - 1, "high"] = aaa.loc[:n - 1, "close"] * 1.02
    aaa.loc[:n - 1, "low"] = aaa.loc[:n - 1, "close"] * 0.98
    return frames, last


def test_x5_long_holds_to_max_hold_swing_takes_the_earlier_reversal(monkeypatch):
    frames, last = _reversal_frames()
    long_sim, long_live, _ = _parity_run(
        frames, last, TradeSettings(holding_style="long", max_hold_days=10), monkeypatch)
    assert long_sim[0] == _day(10) and long_sim[1] == "max_hold"
    _assert_parity(long_sim, long_live)

    frames, last = _reversal_frames()
    swing_sim, swing_live, _ = _parity_run(
        frames, last, TradeSettings(max_hold_days=10), monkeypatch)
    assert swing_sim[1] == "reversal" and swing_sim[0] < long_sim[0]
    _assert_parity(swing_sim, swing_live)


def test_g_guard_x1_bars_without_settings_never_take_profit(monkeypatch):
    o = _entry_open()
    frames, last = _exit_frames([(o * 1.01, o * 1.08, o * 1.00, o * 1.02)])
    sim, live, _ = _parity_run(frames, last, None, monkeypatch)
    assert sim is None or sim[1] != "take_profit"
    assert live is None or live[1] != "take_profit"
    _assert_parity(sim, live)
