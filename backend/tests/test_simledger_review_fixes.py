"""Regression tests for the R1 code-review fix round (batch B1 — money path).
Each test pins one CONFIRMED finding so it cannot regress silently."""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pandas as pd
import pytest

from app.modules.simledger import cycles
from app.modules.simledger.models import Recommendation, SafetyState, SimAccount, SimPosition
from app.modules.simledger.service import (InsufficientCash, SimLedgerService,
                                           _dec, entry_cost_price)

NOW = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)


async def _no_closes(db, account_id, session_date):
    """run_entries also reads today's closed lots (exited_today / slot count)."""
    return []


def _q(price, age_s=0):
    return cycles.QuoteReading(price=price, at=NOW - timedelta(seconds=age_s))


def _acct(cash=2000.0):
    return SimAccount(id=uuid4(), user_id=uuid4(), name="default", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(cash))


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


# --- #26: one InsufficientCash must not poison the cycle --------------------

def test_insufficient_cash_skips_symbol_not_cycle(monkeypatch):
    calls, booked = [], []

    async def fake_open(db, account, **kw):
        calls.append(kw["symbol"])
        if kw["symbol"] == "AAA":
            raise InsufficientCash("AAA: cost > cash")
        booked.append(kw["symbol"])
        return object()

    async def fake_positions(db, account_id):
        return []

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(_no_closes))
    db = _RecSession([_rec("AAA", 1), _rec("BBB", 2)])
    out = asyncio.run(cycles.run_entries(db, _acct(), date(2026, 7, 30),
                                         quote_fn=lambda s: _q(100.0), now=NOW))
    assert calls == ["AAA", "BBB"]      # BBB still attempted after AAA failed
    assert out == ["BBB"]


# --- #26: sizing uses the cost-inclusive price ------------------------------

def test_run_entries_sizes_on_cost_price(monkeypatch):
    seen = {}

    async def fake_open(db, account, **kw):
        seen.update(kw)
        return object()

    async def fake_positions(db, account_id):
        return []

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(_no_closes))
    db = _RecSession([_rec("AAA", 1, stop_distance=5.0, adv=1e9)])
    asyncio.run(cycles.run_entries(db, _acct(cash=2000.0), date(2026, 7, 30),
                                   quote_fn=lambda s: _q(100.0), now=NOW))
    eff = entry_cost_price(100.0, adv=1e9)
    assert eff > 100.0
    # stop derived from the effective price, and booked cost stays within cash
    assert seen["stop"] == pytest.approx(eff - 5.0)
    assert seen["qty"] * eff <= 2000.0 + 1e-6


# --- #34: pyramid re-raises the stop for combined risk ----------------------

def test_pyramid_raises_stop_for_combined_risk():
    acct = _acct(cash=10_000.0)
    pos = SimPosition(id=uuid4(), account_id=acct.id, symbol="AAA", status="open",
                      shares=_dec(10), avg_cost=_dec(100), stop=_dec(97),
                      r_unit=_dec(3), high_water=_dec(100),
                      entry_date=date(2026, 7, 1), adds_done=0,
                      reversal_count=0, bars_held=5)

    class _S:
        def __init__(self):
            self.added = []
            self._results = [None, pos]   # idem miss, open lot found

        async def execute(self, stmt):
            v = self._results.pop(0) if self._results else None

            class _R:
                def __init__(self, val):
                    self._v = val

                def scalar_one_or_none(self):
                    return self._v
            return _R(v)

        def add(self, obj):
            self.added.append(obj)

        async def flush(self):
            for o in self.added:
                if getattr(o, "id", None) is None:
                    o.id = uuid4()

    db = _S()
    asyncio.run(SimLedgerService.open_or_add(
        db, acct, symbol="AAA", qty=10.0, raw_price=110.0, stop=100.0,
        reason="pyramid", idempotency_key="k", trade_date=date(2026, 7, 30),
        equity_for_risk=2000.0))
    # combined risk-at-stop must be within 3% of equity ($60):
    shares, avg, stop = float(pos.shares), float(pos.avg_cost), float(pos.stop)
    assert shares == pytest.approx(20.0)
    assert shares * (avg - stop) <= 2000.0 * 0.03 + 1e-6


# --- #33: stale bars are never re-folded ------------------------------------

def test_daily_exit_skips_stale_bars(monkeypatch):
    pos = SimPosition(id=uuid4(), account_id=uuid4(), symbol="AAA", status="open",
                      shares=_dec(5), avg_cost=_dec(100), stop=_dec(95),
                      r_unit=_dec(5), high_water=_dec(100),
                      entry_date=date(2026, 7, 1), adds_done=0,
                      reversal_count=1, bars_held=7)

    async def fake_positions(db, account_id):
        return [pos]

    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))

    def stale_bars(sym, tf, end):
        ts = pd.DatetimeIndex([pd.Timestamp("2026-07-29 00:00",
                                            tz="America/New_York")]).tz_convert("UTC")
        return pd.DataFrame({"ts": ts, "open": [100.0], "high": [101.0],
                             "low": [99.0], "close": [100.0], "volume": [1],
                             "vwap": [100.0], "trade_count": [1]})

    out = asyncio.run(cycles.daily_exit_management(
        None, _acct(), date(2026, 7, 30), bars_fn=stale_bars))
    assert out.closed == [] and out.data_end == []          # 1 session late < N
    assert pos.bars_held == 7 and pos.reversal_count == 1   # NOT re-folded


# --- #32: one bad symbol never kills the pass -------------------------------

def test_daily_exit_survives_one_bad_symbol(monkeypatch):
    good = SimPosition(id=uuid4(), account_id=uuid4(), symbol="GOOD", status="open",
                       shares=_dec(5), avg_cost=_dec(100), stop=_dec(95),
                       r_unit=_dec(5), high_water=_dec(100),
                       entry_date=date(2026, 7, 1), adds_done=0,
                       reversal_count=0, bars_held=3)
    bad = SimPosition(id=uuid4(), account_id=uuid4(), symbol="BAD", status="open",
                      shares=_dec(5), avg_cost=_dec(100), stop=_dec(95),
                      r_unit=_dec(5), high_water=_dec(100),
                      entry_date=date(2026, 7, 1), adds_done=0,
                      reversal_count=0, bars_held=3)

    async def fake_positions(db, account_id):
        return [bad, good]

    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))

    def bars(sym, tf, end):
        if sym == "BAD":
            raise RuntimeError("corrupt parquet")
        n = 120
        ts = pd.DatetimeIndex([pd.Timestamp("2026-02-01", tz="America/New_York")
                               + pd.Timedelta(days=i) for i in range(n)]).tz_convert("UTC")
        # end the frame ON the session date so the stale-guard passes
        ts = ts[-n:]
        close = [100.0 + i * 0.1 for i in range(n)]
        df = pd.DataFrame({"ts": ts, "open": close,
                           "high": [c * 1.001 for c in close],
                           "low": [c * 0.999 for c in close], "close": close,
                           "volume": [1] * n, "vwap": close, "trade_count": [1] * n})
        df.loc[df.index[-1], "ts"] = pd.Timestamp("2026-07-30 00:00",
                                                  tz="America/New_York").tz_convert("UTC")
        return df

    out = asyncio.run(cycles.daily_exit_management(
        None, _acct(), date(2026, 7, 30), bars_fn=bars))
    assert good.bars_held == 4          # GOOD was still processed


# --- #35: manual pause is never shortened -----------------------------------

def test_protections_never_shorten_manual_pause(monkeypatch):
    acct = _acct()
    manual_until = date(2026, 8, 25)
    state = SafetyState(scope=str(acct.id), halted=False,
                        paused_until=manual_until, peak_equity=_dec(2000))

    class _S:
        def __init__(self):
            # execute #1 -> state row; #2 -> prev snapshot
            class _Snap:
                equity = _dec(2000)
                snapshot_date = date(2026, 7, 29)
            self._results = [state, _Snap()]
            self.added = []

        async def execute(self, stmt):
            v = self._results.pop(0) if self._results else None

            class _R:
                def __init__(self, val):
                    self._v = val

                def scalar_one_or_none(self):
                    return self._v
            return _R(v)

        def add(self, obj):
            self.added.append(obj)

        async def flush(self):
            pass

    out = asyncio.run(cycles.update_protections(_S(), acct, 1900.0,  # -5% day
                                                date(2026, 7, 30)))
    assert out.paused_until == manual_until       # 30d manual pause survives


# --- 2026-09-21: the daily-loss test needs the PREVIOUS SESSION's snapshot ---

def _protections_db(state, snapshot_date):
    class _Snap:
        equity = _dec(2000)

    _Snap.snapshot_date = snapshot_date

    class _S:
        def __init__(self):
            self._results = [state, _Snap()]

        async def execute(self, stmt):
            v = self._results.pop(0) if self._results else None

            class _R:
                def scalar_one_or_none(self):
                    return v
            return _R()

        def add(self, obj):
            pass

        async def flush(self):
            pass

    return _S()


def test_protections_pause_on_the_previous_sessions_snapshot():
    acct = _acct()
    state = SafetyState(scope=str(acct.id), halted=False, peak_equity=_dec(2000))
    out = asyncio.run(cycles.update_protections(
        _protections_db(state, date(2026, 7, 29)), acct, 1900.0,   # -5% day
        date(2026, 7, 30)))
    assert out.paused_until == date(2026, 7, 31)


def test_protections_skip_the_daily_loss_test_across_a_snapshot_gap():
    """A night whose snapshot is missing (worker down, or an equity the cycle
    refused to trust) leaves the newest snapshot two sessions back. Reading that
    multi-session return as one day would pause the next session's entries on a
    loss that never happened — the scoreboard forks on a bookkeeping gap."""
    acct = _acct()
    state = SafetyState(scope=str(acct.id), halted=False, peak_equity=_dec(2000))
    out = asyncio.run(cycles.update_protections(
        _protections_db(state, date(2026, 7, 28)), acct, 1900.0,   # -5% over 2d
        date(2026, 7, 30)))
    assert out.paused_until is None
    # the halt branch is a LEVEL test and must still run on the same equity
    assert out.halted is False and float(out.peak_equity) == 2000.0


def test_protections_halt_still_fires_across_a_snapshot_gap():
    acct = _acct()
    state = SafetyState(scope=str(acct.id), halted=False, peak_equity=_dec(2000))
    out = asyncio.run(cycles.update_protections(
        _protections_db(state, date(2026, 7, 28)), acct, 1500.0,   # -25% from peak
        date(2026, 7, 30)))
    assert out.halted is True and out.paused_until is None


# --- 2026-09-21: the open_once sizing base must not go stale in silence ------

def _snapshot_db(snapshot_date, equity=2000.0):
    class _Snap:
        pass

    _Snap.equity = _dec(equity)
    _Snap.snapshot_date = snapshot_date

    class _S:
        async def execute(self, stmt):
            class _R:
                def scalar_one_or_none(self):
                    return _Snap()
            return _R()

    return _S()


def test_stale_sizing_base_names_the_gap_and_stays_quiet_otherwise():
    acct = _acct()
    today = date(2026, 7, 30)
    assert asyncio.run(cycles.stale_sizing_base(
        _snapshot_db(date(2026, 7, 29)), acct.id, today)) is None
    assert asyncio.run(cycles.stale_sizing_base(
        _snapshot_db(date(2026, 7, 28)), acct.id, today)) == date(2026, 7, 28)


def test_a_snapshot_gap_does_not_change_what_a_fill_is_sized_on(caplog):
    """Reporting only. The simulator sizes on equity_curve[prev_session], so a
    gap IS a divergence — but changing the base live would fork the scoreboard,
    which this work unit may not do. It has to be loud instead of silent."""
    acct = _acct()
    today = date(2026, 7, 30)
    caplog.set_level(logging.ERROR)
    fresh = asyncio.run(cycles._equity_at_prior_close(
        _snapshot_db(date(2026, 7, 29)), acct, today,
        open_positions=[], quote_fn=None, now=NOW))
    gapped = asyncio.run(cycles._equity_at_prior_close(
        _snapshot_db(date(2026, 7, 28)), acct, today,
        open_positions=[], quote_fn=None, now=NOW))
    assert fresh == gapped == 2000.0             # behaviour is unchanged
    assert "sizing base is the 2026-07-28 snapshot" in caplog.text
    assert caplog.text.count("sizing base is the") == 1   # and only for the gap


# --- #31: system account = stable is_system lookup --------------------------

def test_system_account_prefers_existing_is_system_row():
    existing = _acct()

    class _S:
        async def execute(self, stmt):
            class _R:
                def scalar_one_or_none(self):
                    return existing
            return _R()

    out = asyncio.run(SimLedgerService.system_account(_S()))
    assert out is existing          # no user-heuristic resolution, no create


# --- v3.1: intraday chase-cap entry gate ------------------------------------

def test_chase_cap_skips_gap_up_enters_at_ref(monkeypatch):
    """A price >3% above the reco reference is not chased; at/below it enters."""
    import app.modules.simledger.cycles as cyc

    booked = []

    async def fake_open(db, account, **kw):
        booked.append(kw["symbol"])
        return object()

    async def fake_positions(db, account_id):
        return []

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(_no_closes))

    # rec reference price = 100; stop_distance 2, adv big
    def rec(sym):
        return Recommendation(id=uuid4(), symbol=sym, trade_date=date(2026, 7, 30),
                              direction="up", confidence=_dec(70), shortlist_rank=1,
                              phase="up", phase_reason="",
                              features={"stop_distance": 2.0, "adv": 5e7, "price": 100.0})

    class _RS:
        def __init__(self, recs): self._r = recs
        async def execute(self, stmt):
            r = self._r
            class _R:
                def scalars(self):
                    class _S:
                        def all(self): return r
                    return _S()
            return _R()

    # GAP: quote 104 (>+3%) -> skipped
    q = lambda s: cyc.QuoteReading(price=104.0, at=NOW)
    out = asyncio.run(cyc.run_entries(_RS([rec("AAA")]), _acct(), date(2026, 7, 30),
                                      quote_fn=q, now=NOW))
    assert out == [] and booked == []

    # at reference 100 -> enters
    booked.clear()
    q2 = lambda s: cyc.QuoteReading(price=100.0, at=NOW)
    out = asyncio.run(cyc.run_entries(_RS([rec("AAA")]), _acct(), date(2026, 7, 30),
                                      quote_fn=q2, now=NOW))
    assert out == ["AAA"]


def test_chase_cap_none_disables_gate(monkeypatch):
    import app.modules.simledger.cycles as cyc
    booked = []

    async def fake_open(db, account, **kw):
        booked.append(kw["symbol"]); return object()

    async def fake_positions(db, account_id):
        return []

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(_no_closes))

    rec = Recommendation(id=uuid4(), symbol="AAA", trade_date=date(2026, 7, 30),
                         direction="up", confidence=_dec(70), shortlist_rank=1,
                         phase="up", phase_reason="",
                         features={"stop_distance": 2.0, "adv": 5e7, "price": 100.0})

    class _RS:
        async def execute(self, stmt):
            class _R:
                def scalars(self):
                    class _S:
                        def all(self): return [rec]
                    return _S()
            return _R()

    q = lambda s: cyc.QuoteReading(price=120.0, at=NOW)   # +20%, would be capped
    out = asyncio.run(cyc.run_entries(_RS(), _acct(), date(2026, 7, 30),
                                      quote_fn=q, now=NOW, chase_cap=None))
    assert out == ["AAA"]                     # gate disabled -> books anyway
