"""Regression tests for the R1 fix round, batches B4 (efficiency: memoized
bars, gathered quotes) and B5/A2 (fail-closed gates in entry/signal cycles).
Each test pins one CONFIRMED finding so it cannot regress silently."""

import asyncio
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from app.modules.simledger import cycles
from app.modules.simledger.models import (HeartbeatRecord, Recommendation,
                                          SimAccount, SimPosition)
from app.modules.simledger.service import SimLedgerService, _dec
from app.tasks import quant_tasks

NOW = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)
ET = ZoneInfo("America/New_York")


def _q(price, age_s=0):
    return cycles.QuoteReading(price=price, at=NOW - timedelta(seconds=age_s))


def _acct(cash=2000.0):
    return SimAccount(id=uuid4(), user_id=uuid4(), name="default", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(cash))


def _pos(symbol, account_id=None):
    return SimPosition(id=uuid4(), account_id=account_id or uuid4(), symbol=symbol,
                       status="open", shares=_dec(5), avg_cost=_dec(100),
                       stop=_dec(95), r_unit=_dec(5), high_water=_dec(100),
                       entry_date=date(2026, 7, 1), adds_done=0,
                       reversal_count=0, bars_held=3)


def _rec(symbol, rank=1, stop_distance=2.0, adv=5e7):
    return Recommendation(id=uuid4(), symbol=symbol, trade_date=date(2026, 7, 30),
                          direction="up", confidence=_dec(70),
                          shortlist_rank=rank, phase="up", phase_reason="",
                          features={"stop_distance": stop_distance, "adv": adv,
                                    "price": 100.0})


class _RecSession:
    def __init__(self, recs):
        self._recs = recs

    async def execute(self, stmt):
        recs = self._recs

        class _R:
            def scalars(self):
                class _S:
                    def all(self):
                        return recs
                return _S()
        return _R()


# --- #3/#4: fetch_quotes gathers concurrently, omits failures ---------------

def test_fetch_quotes_gathers_and_omits_failures():
    calls = Counter()

    class _Finnhub:
        def quote(self, sym):
            calls[sym] += 1
            if sym == "ERR":
                raise RuntimeError("boom")
            if sym == "NONE":
                return None
            return {"c": 101.5, "t": int(NOW.timestamp())}

    out = asyncio.run(cycles.fetch_quotes(_Finnhub(), ["AAA", "ERR", "NONE", "AAA"]))
    assert set(out) == {"AAA"}                     # failed/None silently omitted
    assert out["AAA"].price == 101.5
    assert out["AAA"].at == NOW
    assert calls["AAA"] == 1                       # deduped: quoted once


# --- #2: per-cycle bar memoization ------------------------------------------

def test_memoized_bars_fn_reads_each_symbol_once():
    calls = []

    def fake_get_bars(sym, tf, *, end=None):
        calls.append((sym, tf, end))
        return pd.DataFrame({"close": [1.0]})

    end = date(2026, 7, 30)
    fn = cycles.memoized_bars_fn(end, get_bars=fake_get_bars)
    a1 = fn("AAA", "1d", end=end)     # build_recommendations call shape
    a2 = fn("AAA", "1d", end=end)     # daily_exit_management call shape
    fn("BBB", "1d", end=end)
    assert [c[0] for c in calls] == ["AAA", "BBB"]
    assert a1 is a2


def test_memoized_bars_fn_does_not_cache_failures():
    calls = []

    def flaky(sym, tf, *, end=None):
        calls.append(sym)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return pd.DataFrame({"close": [1.0]})

    fn = cycles.memoized_bars_fn(date(2026, 7, 30), get_bars=flaky)
    try:
        fn("AAA")
    except RuntimeError:
        pass
    assert not fn("AAA").empty        # retried, then cached
    assert calls == ["AAA", "AAA"]


# --- #3: run_entries quotes each symbol exactly once ------------------------

def test_run_entries_quotes_each_symbol_once(monkeypatch):
    acct = _acct()
    held = _pos("HH", account_id=acct.id)

    async def fake_positions(db, account_id):
        return [held]

    booked = []

    async def fake_open(db, account, **kw):
        booked.append(kw["symbol"])
        return object()

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions",
                        staticmethod(fake_positions))
    counts = Counter()

    def quote_fn(s):
        counts[s] += 1
        return _q(100.0)

    db = _RecSession([_rec("HH", 1), _rec("AAA", 2)])
    asyncio.run(cycles.run_entries(db, acct, date(2026, 7, 30),
                                   quote_fn=quote_fn, now=NOW))
    # held symbol used to be quoted twice (equity mark + entry attempt)
    assert counts == {"HH": 1, "AAA": 1}
    assert "AAA" in booked


# --- #4: check_stops prefetches every open-position quote once --------------

def test_check_stops_quotes_each_position_once(monkeypatch):
    positions = [_pos("AAA"), _pos("BBB")]

    async def fake_positions(db, account_id):
        return positions

    monkeypatch.setattr(SimLedgerService, "get_open_positions",
                        staticmethod(fake_positions))
    counts = Counter()

    def quote_fn(s):
        counts[s] += 1
        return _q(200.0)             # no breach

    out = asyncio.run(cycles.check_stops(None, _acct(), quote_fn=quote_fn, now=NOW))
    assert out == []
    assert counts == {"AAA": 1, "BBB": 1}


# --- A2 fail-closed: entry cycle with no recommendations --------------------

class _FakeDb:
    """execute() always resolves scalar_one_or_none() -> None."""

    def __init__(self):
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        class _R:
            def scalar_one_or_none(self):
                return None
        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


class _FakeSessionFactory:
    def __init__(self):
        self.dbs = []

    def __call__(self):
        db = _FakeDb()
        self.dbs.append(db)

        class _Ctx:
            async def __aenter__(self_inner):
                return db

            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


def test_entry_cycle_fails_closed_without_recommendations(monkeypatch):
    factory = _FakeSessionFactory()
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 7, 30, 9, 40, tzinfo=ET))
    acct = _acct()

    async def fake_system_account(db):
        return acct

    monkeypatch.setattr(SimLedgerService, "system_account",
                        staticmethod(fake_system_account))

    async def fake_state(db, account, **kw):
        return None

    monkeypatch.setattr(cycles, "get_safety_state", fake_state)
    monkeypatch.setattr(cycles, "entries_blocked_reason",
                        lambda state, *, today, **kw: None)

    entered = []

    async def fake_run_entries(*a, **kw):
        entered.append(a)
        return []

    monkeypatch.setattr(cycles, "run_entries", fake_run_entries)
    notes = []

    async def fake_notify(msg):
        notes.append(msg)

    monkeypatch.setattr(quant_tasks, "_notify", fake_notify)

    out = quant_tasks.entry_cycle()
    assert out == "fail-closed: no recommendations"
    assert entered == []                            # nothing was booked
    db = factory.dbs[0]
    assert db.committed
    beats = [o for o in db.added if isinstance(o, HeartbeatRecord)]
    assert len(beats) == 1 and beats[0].name == "entry_cycle"
    assert beats[0].meta == {"fail_closed": "no recommendations"}
    assert len(notes) == 1 and "fail-closed" in notes[0]


# --- A2 fail-closed: signal cycle with mass bar-sync failure ----------------

def test_signal_cycle_fails_closed_on_sync_failures(monkeypatch):
    from quant.data import fetch as qfetch
    from quant.data import sectors as qsectors
    from quant.data import universe as quniverse

    factory = _FakeSessionFactory()
    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", factory)
    monkeypatch.setattr(quant_tasks, "_now_et",
                        lambda: datetime(2026, 7, 30, 17, 30, tzinfo=ET))
    monkeypatch.setattr(quniverse, "constituents_on",
                        lambda d: [f"A{i}" for i in range(10)])
    monkeypatch.setattr(qsectors, "load_sectors", lambda: {})

    def fake_sync_many(symbols, *a, **kw):
        return 0, list(symbols)                     # 100% failure rate

    monkeypatch.setattr(qfetch, "sync_daily_many", fake_sync_many)

    async def fake_system_account(db):
        return None                                 # skip exits/snapshot block

    monkeypatch.setattr(SimLedgerService, "system_account",
                        staticmethod(fake_system_account))

    built, stored = [], []

    def fake_build(*a, **kw):
        built.append(a)
        return []

    async def fake_store(db, recs):
        stored.append(recs)
        return len(recs)

    monkeypatch.setattr(cycles, "build_recommendations", fake_build)
    monkeypatch.setattr(cycles, "store_recommendations", fake_store)
    notes = []

    async def fake_notify(msg):
        notes.append(msg)

    monkeypatch.setattr(quant_tasks, "_notify", fake_notify)

    out = quant_tasks.signal_cycle()
    assert out.startswith("recs=0")
    assert built == [] and stored == []             # NOTHING published
    db = factory.dbs[-1]
    assert db.committed
    beats = [o for o in db.added if isinstance(o, HeartbeatRecord)]
    assert len(beats) == 1 and beats[0].name == "signal_cycle"
    assert beats[0].meta["fail_closed"] == "bar sync failures"
    assert beats[0].meta["recs"] == 0
    assert any("fail-closed" in n for n in notes)
