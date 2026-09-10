"""Phase C #4 — open_once entry semantics, its window gate, and the task wiring.

open_once is the 对照账户 default: D-1 signals filled at D's 09:30 open, sized on
D-1 closing equity and the session's OPENING cash. Every test here pins one of
the three divergences Phase C closes (fill price · equity base · cash base), the
fail-closed behaviour when the open price is missing, or the gate that decides
when the cycle may act at all. The intraday_ladder path must stay untouched —
its own regressions live in test_simledger_cycles / test_phase_a_correctness.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import app.models_registry  # noqa: F401

from app.modules.simledger import cycles
from app.modules.simledger.models import (AccountSnapshot, HeartbeatRecord,
                                          Recommendation, SimAccount, SimOrder,
                                          SimPosition)
from app.modules.simledger.service import SimLedgerService, _dec, entry_cost_price
from app.tasks import quant_tasks

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 7, 30)
NOW = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)
EARLY_CLOSE_DAY = date(2025, 11, 28)      # 13:00 ET half day
ADV = 5e7


def _daily(open_px, *, day=SESSION, close=None):
    ts = pd.DatetimeIndex([pd.Timestamp(day, tz="America/New_York")]).tz_convert("UTC")
    px = open_px if close is None else close
    return pd.DataFrame({"ts": ts, "open": [open_px], "high": [px], "low": [px],
                         "close": [px], "volume": [1], "vwap": [px],
                         "trade_count": [1]})


def _acct(cash=2500.0):
    return SimAccount(id=uuid4(), user_id=uuid4(), name="default", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(cash))


def _rec(symbol, rank=1, stop_distance=2.0, price=100.0):
    return Recommendation(id=uuid4(), symbol=symbol, trade_date=SESSION,
                          direction="up", confidence=Decimal("70"),
                          shortlist_rank=rank, phase="up", phase_reason="",
                          features={"stop_distance": stop_distance, "adv": ADV,
                                    "price": price})


def _snapshot(equity, *, day=date(2026, 7, 29)):
    return AccountSnapshot(id=uuid4(), account_id=uuid4(), snapshot_date=day,
                           equity=_dec(equity), cash=_dec(equity), open_positions=0)


def _pos(symbol, *, avg_cost=100.0, adds_done=0):
    return SimPosition(id=uuid4(), account_id=uuid4(), symbol=symbol, status="open",
                       shares=_dec(5), avg_cost=_dec(avg_cost), stop=_dec(95),
                       r_unit=_dec(5), high_water=_dec(avg_cost),
                       entry_date=date(2026, 7, 1), adds_done=adds_done,
                       reversal_count=0, bars_held=3)


class _Session:
    """Fake session that answers each of run_entries' reads by the entity it
    selects: the recommendation feed, the D-1 snapshot, the idempotency probe,
    and today's sell fills."""

    def __init__(self, recs=(), snapshot=None, sell_fills=(), buy_fills=()):
        self.recs, self.snapshot = list(recs), snapshot
        self.sell_fills, self.buy_fills = list(sell_fills), list(buy_fills)
        self.added = []

    async def execute(self, stmt):
        cols = stmt.column_descriptions
        entity = cols[0].get("entity") if cols else None
        if entity is Recommendation:
            name = cols[0].get("name")
            if name == "symbol":                       # shortlist_symbols
                return _Result(rows=[r.symbol for r in self.recs])
            if name == "id":                           # the fail-closed probe
                return _Result(one=self.recs[0].id if self.recs else None)
            return _Result(rows=self.recs)
        if entity is AccountSnapshot:
            return _Result(one=self.snapshot)
        if entity is SimOrder:
            sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return _Result(all_rows=self.sell_fills if "'sell'" in sql
                           else self.buy_fills)
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


class _Scalars(list):
    def all(self):
        return list(self)


class _Result:
    def __init__(self, rows=(), one=None, all_rows=()):
        self._rows, self._one, self._all = list(rows), one, list(all_rows)

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._one if self._one is not None else \
            (self._rows[0] if self._rows else None)

    def all(self):
        return self._all


def _fill(symbol, raw_price, price=None, qty=1.0):
    return SimpleNamespace(symbol=symbol, raw_price=Decimal(str(raw_price)),
                           price=Decimal(str(price if price is not None else raw_price)),
                           qty=Decimal(str(qty)))


def _run(session, account, *, monkeypatch, open_prices, positions=(), closed=(),
         **kwargs):
    booked = []

    async def fake_open(db, account, **kw):
        booked.append(kw)
        return object()

    async def fake_positions(db, account_id):
        return list(positions)

    async def fake_closed_on(db, account_id, session_date):
        return list(closed)

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions",
                        staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(fake_closed_on))
    out = asyncio.run(cycles.run_entries(
        session, account, SESSION, now=NOW,
        entry_mode=cycles.ENTRY_MODE_OPEN_ONCE, open_prices=open_prices, **kwargs))
    return out, booked


# --- the window gate --------------------------------------------------------

def _et(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)


def test_open_once_window_is_offset_from_the_session_open():
    """The 09:30 bar is only readable after the free-tier SIP delay."""
    assert cycles.in_open_once_window(_et(SESSION, 9, 45)) is False
    assert cycles.in_open_once_window(_et(SESSION, 9, 46)) is True
    assert cycles.in_open_once_window(_et(SESSION, 10, 59)) is True
    assert cycles.in_open_once_window(_et(SESSION, 11, 0)) is False


def test_open_once_window_follows_the_calendar_not_the_wall_clock():
    # a half day opens at 09:30 too, so the window is unchanged...
    assert cycles.in_open_once_window(_et(EARLY_CLOSE_DAY, 9, 46)) is True
    # ...and a non-session never opens it
    assert cycles.in_open_once_window(_et(date(2026, 8, 22), 10, 0)) is False


def test_dead_entry_window_constant_is_gone():
    assert not hasattr(cycles, "ENTRY_WINDOW_ET")
    assert cycles.OPEN_ONCE_WINDOW_MIN == (16, 90)


# --- entry mode resolution --------------------------------------------------

def test_account_entry_mode_defaults_before_and_after_the_column():
    account = _acct()
    assert cycles.account_entry_mode(account) == cycles.ENTRY_MODE_OPEN_ONCE
    account.entry_mode = cycles.ENTRY_MODE_LADDER
    assert cycles.account_entry_mode(account) == cycles.ENTRY_MODE_LADDER


def test_unknown_entry_mode_is_refused(monkeypatch):
    async def fake_positions(db, account_id):
        return []

    async def fake_closed_on(db, account_id, session_date):
        return []

    monkeypatch.setattr(SimLedgerService, "get_open_positions",
                        staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(fake_closed_on))
    with pytest.raises(ValueError, match="unknown entry_mode"):
        asyncio.run(cycles.run_entries(_Session(recs=[_rec("AAA")]), _acct(),
                                       SESSION, now=NOW, entry_mode="teleport"))


# --- #1 fill price: the injected 09:30 open, through the cost model ----------

def test_open_once_fills_at_the_injected_open_price(monkeypatch):
    session = _Session(recs=[_rec("AAA")], snapshot=_snapshot(2000))
    out, booked = _run(session, _acct(), monkeypatch=monkeypatch,
                       open_prices={"AAA": 90.0})
    assert out == ["AAA"] and len(booked) == 1
    assert booked[0]["raw_price"] == 90.0
    assert booked[0]["stop"] == pytest.approx(entry_cost_price(90.0, adv=ADV) - 2.0)


def test_open_once_ignores_the_chase_cap(monkeypatch):
    """The open IS the reference; a gap-up is what the backtest fills at too."""
    session = _Session(recs=[_rec("AAA", price=100.0)], snapshot=_snapshot(2000))
    out, _ = _run(session, _acct(), monkeypatch=monkeypatch,
                  open_prices={"AAA": 130.0}, chase_cap=0.03)
    assert out == ["AAA"]


def test_missing_open_price_keeps_the_symbol_out_for_the_day(monkeypatch, caplog):
    session = _Session(recs=[_rec("AAA", rank=1), _rec("BBB", rank=2)],
                       snapshot=_snapshot(2000))
    out, booked = _run(session, _acct(), monkeypatch=monkeypatch,
                       open_prices={"BBB": 90.0})
    assert out == ["BBB"] and [b["symbol"] for b in booked] == ["BBB"]
    assert "no usable 09:30 open price for AAA" in caplog.text


def test_no_open_prices_at_all_books_nothing(monkeypatch):
    session = _Session(recs=[_rec("AAA")], snapshot=_snapshot(2000))
    out, booked = _run(session, _acct(), monkeypatch=monkeypatch,
                       open_prices={})
    assert out == [] and booked == []


# --- #2 equity base: D-1 close, not a live mark ------------------------------

def _qty_for(equity, cash, open_price=100.0):
    from quant.engine import sizing as qsizing

    entry = entry_cost_price(open_price, adv=ADV)
    return qsizing.position_size(equity, entry, entry - 2.0, risk_pct=0.03,
                                 slots=qsizing.concurrent_slots(equity),
                                 settled_cash=cash, adv=ADV)


def test_open_once_sizes_on_the_prior_close_snapshot(monkeypatch):
    session = _Session(recs=[_rec("AAA")], snapshot=_snapshot(10_000))
    _, booked = _run(session, _acct(cash=2500), monkeypatch=monkeypatch,
                     open_prices={"AAA": 100.0})
    assert booked[0]["qty"] == pytest.approx(_qty_for(10_000, 2500))
    assert booked[0]["equity_for_risk"] == 10_000


def test_missing_snapshot_falls_back_to_a_live_mark_and_says_so(monkeypatch, caplog):
    session = _Session(recs=[_rec("AAA")], snapshot=None)
    account = _acct(cash=2500)
    _, booked = _run(session, account, monkeypatch=monkeypatch,
                     open_prices={"AAA": 100.0})
    # no positions -> the live mark is just cash
    assert booked[0]["equity_for_risk"] == 2500.0
    assert "no account snapshot before" in caplog.text


# --- #3 cash base: the session's OPENING cash --------------------------------

def test_todays_exit_proceeds_do_not_fund_a_0930_fill(monkeypatch):
    """The backtest funds entries from cash as of the open (simulator's
    cash_at_open); a lot sold at 11:00 cannot buy something at 09:30."""
    session = _Session(recs=[_rec("AAA")], snapshot=_snapshot(10_000),
                       sell_fills=[_fill("OLD", 100.0, price=100.0, qty=20.0)])
    _, booked = _run(session, _acct(cash=2500), monkeypatch=monkeypatch,
                     open_prices={"AAA": 100.0})
    assert booked[0]["qty"] == pytest.approx(_qty_for(10_000, 500.0))


def test_no_cash_left_at_the_open_books_nothing(monkeypatch):
    session = _Session(recs=[_rec("AAA")], snapshot=_snapshot(10_000),
                       sell_fills=[_fill("OLD", 100.0, price=100.0, qty=25.0)])
    out, booked = _run(session, _acct(cash=2500), monkeypatch=monkeypatch,
                       open_prices={"AAA": 100.0})
    assert out == [] and booked == []


# --- the platform may only tighten the ladder --------------------------------

def test_max_slots_tightens_the_equity_ladder(monkeypatch):
    from quant.engine import sizing as qsizing

    recs = [_rec(f"S{i}", rank=i + 1) for i in range(4)]
    prices = {f"S{i}": 100.0 for i in range(4)}
    session = _Session(recs=recs, snapshot=_snapshot(20_000))
    assert qsizing.concurrent_slots(20_000) == 10
    out, _ = _run(session, _acct(cash=1_000_000), monkeypatch=monkeypatch,
                  open_prices=prices, max_slots=2)
    assert out == ["S0", "S1"]


def test_max_slots_narrows_the_gate_without_enlarging_the_positions(monkeypatch):
    """The equity ladder sets both the concurrency gate AND the per-position
    dollar cap (equity/slots). Folding the platform cap into the sizing divisor
    would make the surviving position BIGGER — the opposite of tightening."""
    recs = [_rec("S0", rank=1), _rec("S1", rank=2)]
    prices = {"S0": 100.0, "S1": 100.0}
    session = _Session(recs=recs, snapshot=_snapshot(2_000))
    out, booked = _run(session, _acct(cash=2_000), monkeypatch=monkeypatch,
                       open_prices=prices, max_slots=1)
    assert out == ["S0"]                                   # the gate narrowed...
    entry = entry_cost_price(100.0, adv=ADV)
    # ...and the lot is still the $2,000/3 ladder slice, not $2,000/1
    assert booked[0]["qty"] * entry == pytest.approx(2_000 / 3, rel=1e-9)


def test_max_slots_never_loosens_the_ladder(monkeypatch):
    recs = [_rec(f"S{i}", rank=i + 1) for i in range(5)]
    prices = {f"S{i}": 100.0 for i in range(5)}
    session = _Session(recs=recs, snapshot=_snapshot(2_000))
    out, _ = _run(session, _acct(cash=1_000_000), monkeypatch=monkeypatch,
                  open_prices=prices, max_slots=10)
    assert out == ["S0", "S1", "S2"]              # the $2k ladder tier, 3 slots


# --- pyramids: the add gate and the combined-risk stop ----------------------

def test_open_once_gates_the_add_on_the_raw_open(monkeypatch):
    """simulator.py gates on the RAW open; pricing the gate cost-inclusively let
    an add through at a price the backtest would have refused."""
    entry = entry_cost_price(100.0, adv=ADV)
    held = _pos("AAA", avg_cost=(100.0 + entry) / 2)       # between raw and eff
    session = _Session(recs=[_rec("AAA")], snapshot=_snapshot(10_000))
    out, _ = _run(session, _acct(), monkeypatch=monkeypatch,
                  open_prices={"AAA": 100.0}, positions=[held])
    assert out == []                                       # raw 100 <= avg_cost

    held.avg_cost = _dec(99.0)                             # a genuine winner
    out, booked = _run(session, _acct(), monkeypatch=monkeypatch,
                       open_prices={"AAA": 100.0}, positions=[held])
    assert out == ["AAA"] and booked[0]["reason"] == "pyramid"


def test_tightened_risk_pct_reaches_the_pyramid_stop_raise(monkeypatch):
    """After an add the stop must keep COMBINED risk inside the budget; the
    budget is the tightened one, not the constant (simulator passes cfg.risk_pct
    into the same call)."""
    held = _pos("AAA", avg_cost=90.0)
    session = _Session(recs=[_rec("AAA")], snapshot=_snapshot(10_000))
    _, booked = _run(session, _acct(), monkeypatch=monkeypatch,
                     open_prices={"AAA": 100.0}, positions=[held],
                     risk_pct=0.005)
    assert booked[0]["risk_pct"] == 0.005


# --- the ladder path is untouched -------------------------------------------

def test_ladder_mode_still_prices_off_the_quote(monkeypatch):
    booked = []

    async def fake_open(db, account, **kw):
        booked.append(kw)
        return object()

    async def fake_positions(db, account_id):
        return []

    async def fake_closed_on(db, account_id, session_date):
        return []

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions",
                        staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(fake_closed_on))
    out = asyncio.run(cycles.run_entries(
        _Session(recs=[_rec("AAA")]), _acct(), SESSION, now=NOW,
        quote_fn=lambda s: cycles.QuoteReading(price=101.0, at=NOW),
        entry_mode=cycles.ENTRY_MODE_LADDER, open_prices={"AAA": 1.0}))
    assert out == ["AAA"] and booked[0]["raw_price"] == 101.0


# --- entry_cycle wiring -----------------------------------------------------

class _FakeDb(_Session):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.committed = False
        self.heartbeat_meta = {}

    async def execute(self, stmt):
        cols = stmt.column_descriptions
        entity = cols[0].get("entity") if cols else None
        if entity is HeartbeatRecord:
            beats = [o for o in self.added if isinstance(o, HeartbeatRecord)]
            return _Result(one=beats[-1] if beats else None)
        return await super().execute(stmt)

    async def commit(self):
        self.committed = True


class _Factory:
    """One session per cycle. A single db reused across cycles is what makes the
    heartbeat meta behave like the real row does between beats."""

    def __init__(self, dbs):
        self._dbs = list(dbs)
        self.used = []

    def __call__(self):
        db = self._dbs.pop(0) if len(self._dbs) > 1 else self._dbs[0]
        self.used.append(db)

        class _Ctx:
            async def __aenter__(self_inner):
                return db

            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


@pytest.fixture
def wired(monkeypatch):
    """entry_cycle with its account, protections and booking stubbed out."""
    account = _acct()
    notes = []

    async def fake_system_account(db):
        return account

    async def fake_state(db, acct, **kw):
        return None

    async def fake_notify(msg):
        notes.append(msg)
        return True

    async def fake_run_entries(*a, **kw):
        return []

    monkeypatch.setattr(SimLedgerService, "system_account",
                        staticmethod(fake_system_account))
    monkeypatch.setattr(cycles, "get_safety_state", fake_state)
    monkeypatch.setattr(cycles, "entries_blocked_reason",
                        lambda state, *, today, **kw: None)
    monkeypatch.setattr(cycles, "run_entries", fake_run_entries)
    monkeypatch.setattr(quant_tasks, "_notify", fake_notify)
    monkeypatch.setattr(quant_tasks, "FinnhubClient", lambda *a, **kw: object())
    return SimpleNamespace(account=account, notes=notes, monkeypatch=monkeypatch)


def _at(hh, mm):
    return lambda: datetime(2026, 7, 30, hh, mm, tzinfo=ET)


def test_entry_cycle_waits_for_the_open_once_window(wired, monkeypatch):
    monkeypatch.setattr(quant_tasks, "_now_et", _at(9, 40))
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _Factory([_FakeDb()]))
    assert quant_tasks.entry_cycle() == "skipped: outside the open_once window"


def test_ladder_account_is_not_gated_by_the_open_once_window(wired, monkeypatch):
    wired.account.entry_mode = cycles.ENTRY_MODE_LADDER
    db = _FakeDb(recs=[_rec("AAA")])
    monkeypatch.setattr(quant_tasks, "_now_et", _at(14, 0))
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _Factory([db]))
    assert quant_tasks.entry_cycle() == "nothing to book"


def _wire_open_prices(monkeypatch, prices):
    from quant.data import fetch as qfetch

    monkeypatch.setattr(qfetch, "session_open_prices",
                        lambda symbols, day, **kw: dict(prices))


def _beats(db):
    return [o for o in db.added if isinstance(o, HeartbeatRecord)]


def _three_cycles(monkeypatch, db):
    monkeypatch.setattr(quant_tasks, "_now_et", _at(9, 50))
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _Factory([db]))
    _wire_open_prices(monkeypatch, {})
    flags = []
    for _ in range(3):
        quant_tasks.entry_cycle()
        flags.append(_beats(db)[0].meta.get("open_price_alert"))
    return flags


def test_missing_open_price_alerts_once_per_day(wired, monkeypatch):
    """Five cycles fall inside the window and _beat replaces meta wholesale, so
    the flag has to be carried forward on every one of them — not just stamped
    on the cycle that sent."""
    db = _FakeDb(recs=[_rec("AAA")])
    flags = _three_cycles(monkeypatch, db)
    assert flags == [str(SESSION)] * 3
    assert _beats(db)[0].meta["missing_open"] == ["AAA"]
    assert len(wired.notes) == 1 and "09:30" in wired.notes[0]


def test_a_failed_alert_does_not_burn_the_day(wired, monkeypatch):
    """Stamping the flag before the send lost the whole day's alert to one
    Telegram failure — exactly when it matters (watchdog._alert's rule)."""
    outcomes = [False, True, True]

    async def _flaky(msg):
        wired.notes.append(msg)
        return outcomes.pop(0)

    monkeypatch.setattr(quant_tasks, "_notify", _flaky)
    db = _FakeDb(recs=[_rec("AAA")])
    flags = _three_cycles(monkeypatch, db)
    # cycle 1 failed to send -> no flag; cycle 2 retried and landed; cycle 3 quiet
    assert flags == [None, str(SESSION), str(SESSION)]
    assert len(wired.notes) == 2


def test_outside_the_window_still_beats(wired, monkeypatch):
    """The beat fires every 15 min while the window is 74 minutes wide — silence
    outside it would read to the watchdog as a stalled entry cycle."""
    db = _FakeDb()
    monkeypatch.setattr(quant_tasks, "_now_et", _at(9, 40))
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _Factory([db]))
    assert quant_tasks.entry_cycle() == "skipped: outside the open_once window"
    beat = [o for o in db.added if isinstance(o, HeartbeatRecord)][0]
    assert beat.meta == {"skipped": "outside_open_once_window"}
    assert db.committed


def test_rejected_settings_alert_from_the_entry_cycle(wired, monkeypatch):
    from app.modules.simledger import settings as settings_mod

    async def fake_settings(db):
        return settings_mod.EffectiveSettings(
            per_trade_risk_pct=0.03, daily_loss_pause_pct=0.02,
            portfolio_drawdown_halt_pct=0.15, min_confidence=65.0,
            intraday_entry_chase_cap=0.03, max_concurrent_slots=10,
            rejected=("per_trade_risk_pct=0.5 is LOOSER than 0.03",))

    monkeypatch.setattr(quant_tasks, "effective_settings", fake_settings)
    monkeypatch.setattr(quant_tasks, "_now_et", _at(9, 50))
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal",
                        _Factory([_FakeDb(recs=[_rec("AAA")])]))
    _wire_open_prices(monkeypatch, {"AAA": 100.0})

    quant_tasks.entry_cycle()
    assert any("master_settings" in n for n in wired.notes)


def test_open_price_fetch_failure_fails_closed(wired, monkeypatch):
    from quant.data import fetch as qfetch

    def boom(symbols, day, **kw):
        raise RuntimeError("alpaca down")

    db = _FakeDb(recs=[_rec("AAA")])
    monkeypatch.setattr(qfetch, "session_open_prices", boom)
    monkeypatch.setattr(quant_tasks, "_now_et", _at(9, 50))
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _Factory([db]))

    assert quant_tasks.entry_cycle() == "nothing to book"
    assert any("fail-closed" in n for n in wired.notes)


# --- end-of-day marks: the session's own close, not a stale quote ------------

def _marks(positions, frames, *, quote_fn=None):
    def bars_fn(sym, tf, *, end):
        if sym not in frames:
            raise RuntimeError("no bars")
        return frames[sym]

    return asyncio.run(cycles.closing_marks(positions, SESSION, bars_fn=bars_fn,
                                            quote_fn=quote_fn))


def test_snapshot_marks_at_the_sessions_daily_close(caplog):
    """The backtest marks equity at D's close; a post-close Finnhub quote is
    >15 min stale by design and was silently the primary source."""
    caplog.set_level(logging.INFO)
    marks = _marks([_pos("AAA")], {"AAA": _daily(123.0)},
                   quote_fn=lambda s: cycles.QuoteReading(price=999.0, at=NOW))
    assert marks == {"AAA": 123.0}
    assert "daily close: ['AAA']" in caplog.text


def test_snapshot_falls_back_to_the_quote_then_to_cost(caplog):
    caplog.set_level(logging.INFO)
    positions = [_pos("STALE"), _pos("GONE")]
    frames = {"STALE": _daily(50.0, day=date(2026, 7, 29))}   # not this session

    def quote_fn(sym):
        return cycles.QuoteReading(price=77.0, at=NOW) if sym == "STALE" else None

    marks = _marks(positions, frames, quote_fn=quote_fn)
    assert marks == {"STALE": 77.0}                # GONE is left to avg_cost
    assert "quote: ['STALE']" in caplog.text and "avg_cost: ['GONE']" in caplog.text


def test_snapshot_ignores_raw_prices():
    frame = _daily(50.0)
    frame.attrs["unadjusted"] = True               # an unsynced split
    assert _marks([_pos("AAA")], {"AAA": frame}) == {}


# --- the reconciliation sentinel --------------------------------------------

def _drift(fills, frames):
    session = _Session(buy_fills=fills)
    return asyncio.run(cycles.open_fill_drift(
        session, _acct(), SESSION, bars_fn=lambda s, tf, end: frames[s]))


def test_fill_matching_the_daily_open_is_quiet():
    assert _drift([_fill("AAA", 100.05)], {"AAA": _daily(100.0)}) == []   # 5bps


def test_fill_off_the_daily_open_is_reported(caplog):
    out = _drift([_fill("AAA", 100.5)], {"AAA": _daily(100.0)})           # 50bps
    assert len(out) == 1 and "AAA" in out[0] and "+50.0bps" in out[0]
    assert "fill reconciliation" in caplog.text


def test_stale_daily_bar_is_not_reconciled():
    assert _drift([_fill("AAA", 100.5)],
                  {"AAA": _daily(100.0, day=date(2026, 7, 29))}) == []
