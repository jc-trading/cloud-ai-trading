"""Regression tests for the R1 code-review fix round (batch B1 — money path).
Each test pins one CONFIRMED finding so it cannot regress silently."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pandas as pd
import pytest

from app.modules.simledger import cycles
from app.modules.simledger.models import Recommendation, SafetyState, SimAccount, SimPosition
from app.modules.simledger.service import (InsufficientCash, SimLedgerService,
                                           _dec, entry_cost_price)

NOW = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)


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
    assert out == []
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
