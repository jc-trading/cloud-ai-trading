"""Phase C decision 5 — open_once IS the backtest's entry step.

One set of synthetic daily bars feeds both the R0-9 simulator and the live
``run_entries(entry_mode="open_once")``. Same D-1 signals, same D open, same
D-1 equity, same opening cash: the two must produce the same symbols, share
counts, entry prices and stops. This is the test that would catch the fixed_oos
scoreboard and the 对照账户 drifting apart again.

Offline: the bar reader, the ledger session and the position constructor are all
stubbed; nothing touches the network or a database.
"""

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
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
from quant.engine.exits import Position

D0 = date(2026, 8, 16)                    # the D-2 session (a lot is opened at D1)
D1 = date(2026, 8, 17)                    # signals decided on this close
D2 = date(2026, 8, 18)                    # filled at this open
NOW = datetime(2026, 8, 18, 13, 50, tzinfo=timezone.utc)
CAPITAL = 2000.0
# The funnel itself is not what parity is about; a linear synthetic ramp scores
# a confidence the deployed 65 threshold would reject, so both sides run the
# same loosened funnel (same choice as test_phase_a_correctness).
PARITY_FUNNEL = qfunnel.FunnelParams(min_confidence=0.0, atr_pct_min=0.0)
SECTORS = {"AAA": "tech", "BBB": "energy", "CCC": "health", "SPY": "etf"}
LEDGER_DP = 1e-6                          # sim_positions columns are Numeric(18,6)


def _frame(closes, *, last_day=D2, vol=50_000_000):
    n = len(closes)
    ts = pd.DatetimeIndex([pd.Timestamp(last_day, tz="America/New_York")
                           - pd.Timedelta(days=n - 1 - i)
                           for i in range(n)]).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts,
        # the open is NOT the close: an entry priced off the close would pass a
        # parity test that this one has to fail
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
        "SPY": _frame([100.0] * 130),          # flat: never shortlisted
    }


# --- the backtest side ------------------------------------------------------

def _simulator_entries(frames, monkeypatch, *, symbols=("AAA", "BBB", "CCC"),
                       start=D1, capital=CAPITAL):
    """Run the simulator and capture every lot it opens. Position is constructed
    exactly once per new lot, so wrapping it is the cheapest honest read of the
    entry step."""
    symbols = list(symbols)
    created = []

    class _Recorded(Position):
        def __init__(self, **kw):
            created.append(dict(kw))
            super().__init__(**kw)

    monkeypatch.setattr(simulator.barsmod, "get_bars",
                        lambda symbol, tf="1d", **kw: frames[symbol])
    monkeypatch.setattr(simulator, "Position", _Recorded)
    cfg = simulator.SimConfig(start=str(start), end=str(D2),
                              starting_capital=capital, funnel=PARITY_FUNNEL)
    res = simulator.run(symbols, SECTORS, cfg)
    return created, res


# --- the live side ----------------------------------------------------------

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
    """Enough of an AsyncSession for the REAL SimLedgerService to book against:
    the cash arithmetic under test must be the service's own, not a stub's."""

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
            # already_booked probes one id; cash_from_exits_on reads the fills
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


def _live_entries(frames, monkeypatch):
    at_d1 = {s: f.iloc[:-1].reset_index(drop=True) for s, f in frames.items()}
    batch = cycles.build_recommendations(
        ["AAA", "BBB", "CCC"], D1, funnel_params=PARITY_FUNNEL,
        bars_fn=lambda s, tf, end: at_d1[s], sectors=SECTORS)
    recs = [Recommendation(id=uuid4(), **r) for r in batch.rows
            if r["shortlist_rank"] is not None]
    recs.sort(key=lambda r: r.shortlist_rank)
    assert recs, "the shortlist must not be empty or the test proves nothing"

    account = SimAccount(id=uuid4(), user_id=uuid4(), name="system", is_system=True,
                         starting_capital=_dec(CAPITAL), cash=_dec(CAPITAL))
    snapshot = AccountSnapshot(id=uuid4(), account_id=account.id, snapshot_date=D1,
                               equity=_dec(CAPITAL), cash=_dec(CAPITAL),
                               open_positions=0)
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
        session, account, D2, now=NOW, entry_mode=cycles.ENTRY_MODE_OPEN_ONCE,
        open_prices=open_prices))
    lots = [o for o in session.added if isinstance(o, SimPosition)]
    return booked, lots, account


def _as_rows(lots):
    return sorted(({"symbol": p.symbol, "shares": float(p.shares),
                    "avg_cost": float(p.avg_cost), "stop": float(p.stop),
                    "r_unit": float(p.r_unit)} for p in lots),
                  key=lambda r: r["symbol"])


def _sim_rows(created):
    return sorted(({"symbol": p["symbol"], "shares": float(p["shares"]),
                    "avg_cost": float(p["avg_cost"]), "stop": float(p["stop"]),
                    "r_unit": float(p["r_unit"])} for p in created),
                  key=lambda r: r["symbol"])


def test_open_once_reproduces_the_backtest_entry_step(monkeypatch):
    frames = _bars()
    sim = _sim_rows(_simulator_entries(frames, monkeypatch)[0])
    booked, lots, _ = _live_entries(frames, monkeypatch)

    assert [r["symbol"] for r in sim] == sorted(booked)
    live = _as_rows(lots)
    assert len(live) == len(sim) > 0
    # LEDGER_DP: the only legal difference is the sim_positions schema rounding
    # to Numeric(18,6); anything above that is a semantic divergence
    for got, want in zip(live, sim):
        assert got["symbol"] == want["symbol"]
        assert got["shares"] == pytest.approx(want["shares"], abs=LEDGER_DP)
        assert got["avg_cost"] == pytest.approx(want["avg_cost"], abs=LEDGER_DP)
        assert got["stop"] == pytest.approx(want["stop"], abs=LEDGER_DP)
        assert got["r_unit"] == pytest.approx(want["r_unit"], abs=LEDGER_DP)


def test_entry_date_and_slot_count_match_the_ladder(monkeypatch):
    frames = _bars()
    created, _ = _simulator_entries(frames, monkeypatch)
    _, lots, _ = _live_entries(frames, monkeypatch)
    from quant.engine import sizing as qsizing

    assert {p["entry_date"] for p in created} == {D2}
    assert {p.entry_date for p in lots} == {D2}
    assert len(lots) <= qsizing.concurrent_slots(CAPITAL)


def test_the_fill_is_the_open_not_the_prior_close(monkeypatch):
    """Guards the guard: if run_entries silently fell back to the D-1 close the
    parity assertions above would still pass on a frame where open == close."""
    frames = _bars()
    _, lots, _ = _live_entries(frames, monkeypatch)
    from app.modules.simledger.service import entry_cost_price

    for lot in lots:
        f = frames[lot.symbol]
        adv = float((f["close"] * f["volume"]).rolling(20).mean().iloc[-2])
        assert float(lot.avg_cost) == pytest.approx(
            entry_cost_price(float(f["open"].iloc[-1]), adv=adv), abs=LEDGER_DP)
        assert float(lot.avg_cost) != pytest.approx(float(f["close"].iloc[-2]))


# --- scenario 2: a live book, not a clean-slate account ---------------------
#
# Scenario 1 enters from cash with nothing held, so equity_ref == cash and the
# two sizing bases are indistinguishable. Here they are not: a lot opened at D1
# marks the D-1 equity away from cash, and that same lot is stopped out
# intraday on D2 (position_cycle at ~09:35) BEFORE the 09:46 entry cycle — so
# the session's opening cash is strictly less than the cash on hand.

CAPITAL_2 = 5_000.0                       # 4 ladder slots, so cash gates the tail


def _bars_two_day_book():
    """AAA trades on D0 (so it is entered at D1's open) and gaps through its stop
    on D2. CCC/DDD/EEE have NO D0 bar, so they cannot be entered until D2 — they
    are the NEW lots whose sizing this scenario is about."""
    aaa = _frame([50 + i * 0.80 for i in range(130)])
    crash = float(aaa["close"].iloc[-2]) * 0.6
    aaa.loc[aaa.index[-1], ["open", "high", "low", "close"]] = [
        crash * 0.995, crash * 1.001, crash * 0.999, crash]

    frames = {"AAA": aaa, "SPY": _frame([100.0] * 130)}
    for sym, base, slope in (("CCC", 30.0, 0.35), ("DDD", 28.0, 0.33),
                             ("EEE", 26.0, 0.31)):
        f = _frame([base + i * slope for i in range(130)])
        on = f["ts"].dt.tz_convert("America/New_York").dt.date
        frames[sym] = f[on != D0].reset_index(drop=True)
    return frames


def _book_at_d2(created, res):
    """The simulator's own state at the moment D2's entries are sized, read off
    its public outputs: D-1 closing equity, the cash the exit released, and the
    cash on hand once it has."""
    entry_cost = sum(p["shares"] * p["avg_cost"] for p in created
                     if p["entry_date"] == D1)
    exits = [t for t in res.trades if t.exit_date == D2]
    proceeds = sum(t.shares * t.exit_price for t in exits)
    return SimpleNamespace(
        equity_ref=float(res.equity.loc[D1]),
        proceeds=float(proceeds),
        cash=CAPITAL_2 - float(entry_cost) + float(proceeds),
        exited=[t.symbol for t in exits])


def _live_entries_two_day_book(frames, monkeypatch, book, *, snapshot=True,
                               proceeds=None):
    symbols = ["AAA", "CCC", "DDD", "EEE"]
    at_d1 = {s: f.iloc[:-1].reset_index(drop=True) for s, f in frames.items()}
    batch = cycles.build_recommendations(
        symbols, D1, funnel_params=PARITY_FUNNEL,
        bars_fn=lambda s, tf, end: at_d1[s], sectors=SECTORS)
    recs = sorted([Recommendation(id=uuid4(), **r) for r in batch.rows
                   if r["shortlist_rank"] is not None],
                  key=lambda r: r.shortlist_rank)

    account = SimAccount(id=uuid4(), user_id=uuid4(), name="system", is_system=True,
                         starting_capital=_dec(CAPITAL_2), cash=_dec(book.cash))
    snap = AccountSnapshot(id=uuid4(), account_id=account.id, snapshot_date=D1,
                           equity=_dec(book.equity_ref), cash=_dec(book.cash),
                           open_positions=1) if snapshot else None
    released = book.proceeds if proceeds is None else proceeds
    session = _Session(recs, snap)
    session.sell_fills = [SimpleNamespace(symbol=s, price=_dec(released),
                                          raw_price=_dec(released), qty=_dec(1))
                          for s in book.exited]

    closed = [SimPosition(id=uuid4(), account_id=account.id, symbol=s,
                          status="closed", shares=_dec(1), avg_cost=_dec(1),
                          stop=_dec(1), r_unit=_dec(1), high_water=_dec(1),
                          entry_date=D1, adds_done=0, reversal_count=0,
                          bars_held=1)
              for s in book.exited]

    async def _none(db, account_id):
        return []

    async def _closed_on(db, account_id, session_date):
        return closed

    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(_none))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(_closed_on))

    open_prices = {s: float(f["open"].iloc[-1]) for s, f in frames.items()}
    booked = asyncio.run(cycles.run_entries(
        session, account, D2, now=NOW, entry_mode=cycles.ENTRY_MODE_OPEN_ONCE,
        open_prices=open_prices))
    lots = [o for o in session.added if isinstance(o, SimPosition)]
    return booked, lots


def _two_day_run(monkeypatch, **live_kwargs):
    frames = _bars_two_day_book()
    created, res = _simulator_entries(
        frames, monkeypatch, symbols=("AAA", "CCC", "DDD", "EEE"),
        start=D0, capital=CAPITAL_2)
    book = _book_at_d2(created, res)
    sim = _sim_rows([p for p in created if p["entry_date"] == D2])
    booked, lots = _live_entries_two_day_book(frames, monkeypatch, book,
                                              **live_kwargs)
    return SimpleNamespace(book=book, sim=sim, booked=booked,
                           live=_as_rows(lots))


def _assert_matches(run):
    assert [r["symbol"] for r in run.sim] == sorted(run.booked)
    assert len(run.live) == len(run.sim) > 0
    for got, want in zip(run.live, run.sim):
        assert got["symbol"] == want["symbol"]
        assert got["shares"] == pytest.approx(want["shares"], abs=LEDGER_DP)
        assert got["avg_cost"] == pytest.approx(want["avg_cost"], abs=LEDGER_DP)
        assert got["stop"] == pytest.approx(want["stop"], abs=LEDGER_DP)
        assert got["r_unit"] == pytest.approx(want["r_unit"], abs=LEDGER_DP)


def test_open_once_matches_with_a_held_lot_and_an_intraday_exit(monkeypatch):
    run = _two_day_run(monkeypatch)
    # the scenario is only meaningful if the two bases really differ
    assert run.book.proceeds > 0
    assert run.book.equity_ref != pytest.approx(run.book.cash)
    assert run.book.exited == ["AAA"]
    assert "AAA" not in run.booked                 # exited today -> no re-entry
    _assert_matches(run)


def test_at_least_one_entry_is_sized_by_equity_not_cash(monkeypatch):
    """Without this the scenario could pass while every lot was cash-capped, and
    the D-1 equity base would never actually drive a share count."""
    from quant.engine import sizing as qsizing

    run = _two_day_run(monkeypatch)
    cash_at_open = run.book.cash - run.book.proceeds
    notionals = [r["shares"] * r["avg_cost"] for r in run.live]
    ladder_cap = run.book.equity_ref / qsizing.concurrent_slots(run.book.equity_ref)
    # the biggest lot is the ladder slice off D-1 equity, well inside the cash...
    assert max(notionals) == pytest.approx(ladder_cap, rel=1e-6)
    assert max(notionals) < cash_at_open - 1.0
    # ...and the last one really is bound by the session's opening cash
    assert min(notionals) < ladder_cap - 1.0


def test_ignoring_todays_exit_proceeds_breaks_parity(monkeypatch):
    """Mutation: fund entries from the cash on hand instead of the session's
    opening cash. If this still matched, the cash_at_open path would be untested."""
    run = _two_day_run(monkeypatch, proceeds=0.0)
    with pytest.raises(AssertionError):
        _assert_matches(run)


def test_falling_back_off_the_prior_snapshot_breaks_parity(monkeypatch):
    """Mutation: no D-1 snapshot, so run_entries sizes on a live mark."""
    run = _two_day_run(monkeypatch, snapshot=False)
    with pytest.raises(AssertionError):
        _assert_matches(run)
