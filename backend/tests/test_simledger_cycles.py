"""Cycle-logic tests (R1-2/R1-3): protections gate, stale-quote guard,
recommendation building from synthetic bars, entry slot gates, intraday
stop-breach pass. Service booking is monkeypatched — its own tests cover it."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest

from app.modules.simledger import cycles
from app.modules.simledger.models import Recommendation, SafetyState, SimAccount, SimPosition
from app.modules.simledger.service import SimLedgerService, _dec

NOW = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)


def _q(price, age_s=0):
    return cycles.QuoteReading(price=price, at=NOW - timedelta(seconds=age_s))


def test_quote_staleness_guard():
    assert cycles.quote_is_usable(_q(100.0), now=NOW)
    assert not cycles.quote_is_usable(None, now=NOW)
    assert not cycles.quote_is_usable(_q(0.0), now=NOW)
    assert not cycles.quote_is_usable(_q(100.0, age_s=16 * 60), now=NOW)


def test_entries_blocked_reasons(tmp_path):
    today = date(2026, 7, 30)
    sentinel = str(tmp_path / "HALT")
    assert cycles.entries_blocked_reason(None, today=today, sentinel_path=sentinel) is None
    open(sentinel, "w").close()
    assert "sentinel" in cycles.entries_blocked_reason(None, today=today,
                                                       sentinel_path=sentinel)
    state = SafetyState(scope="global", halted=True, halted_until=None, reason="dd")
    assert "halted" in cycles.entries_blocked_reason(state, today=today,
                                                     sentinel_path=str(tmp_path / "x"))
    state = SafetyState(scope="global", halted=False,
                        paused_until=date(2026, 7, 30))
    assert "pause" in cycles.entries_blocked_reason(state, today=today,
                                                    sentinel_path=str(tmp_path / "x"))
    state = SafetyState(scope="global", halted=False, paused_until=date(2026, 7, 29))
    assert cycles.entries_blocked_reason(state, today=today,
                                         sentinel_path=str(tmp_path / "x")) is None


def _bars(closes, vol=50_000_000):
    n = len(closes)
    ts = pd.DatetimeIndex([pd.Timestamp("2026-01-02", tz="America/New_York")
                           + pd.Timedelta(days=i) for i in range(n)]).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes], "close": closes,
        "volume": [vol] * n, "vwap": closes, "trade_count": [100] * n,
    })


def test_build_recommendations_shortlists_uptrend():
    frames = {
        "UPP": _bars([50 + i * 0.8 for i in range(120)]),      # clean uptrend
        "FLT": _bars([50.0 + (0.01 if i % 2 else -0.01) for i in range(120)]),
    }
    recs = cycles.build_recommendations(
        ["UPP", "FLT"], date(2026, 7, 30),
        funnel_params=cycles.qfunnel.FunnelParams(min_confidence=0.0,
                                                  atr_pct_min=0.0),
        bars_fn=lambda s, tf, end: frames[s])
    by_sym = {r["symbol"]: r for r in recs}
    assert by_sym["UPP"]["shortlist_rank"] == 1
    assert by_sym["UPP"]["phase"] == "up"
    assert by_sym["UPP"]["trade_date"] == date(2026, 7, 31)    # next session
    assert by_sym["UPP"]["features"]["stop_distance"] > 0
    # the noise series may squeak through as a weak up-signal, but it must
    # never be SHORTLISTED (trend-alignment filter kills it)
    assert by_sym.get("FLT") is None or by_sym["FLT"]["shortlist_rank"] is None


class _RecSession:
    """Fake session: first execute returns the recommendations list."""

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


def _rec(symbol, rank=1, stop_distance=2.0, adv=5e7):
    return Recommendation(id=uuid4(), symbol=symbol, trade_date=date(2026, 7, 30),
                          direction="up", confidence=Decimal("70"),
                          shortlist_rank=rank, phase="up", phase_reason="",
                          features={"stop_distance": stop_distance, "adv": adv,
                                    "price": 100.0})


def _acct(cash=2000.0):
    return SimAccount(id=uuid4(), user_id=uuid4(), name="default", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(cash))


def test_run_entries_books_and_skips_stale(monkeypatch):
    booked = []

    async def fake_open(db, account, **kw):
        booked.append(kw["symbol"])
        return object()

    async def fake_positions(db, account_id):
        return []

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    quotes = {"AAA": _q(100.0), "BBB": _q(100.0, age_s=20 * 60)}  # BBB stale
    db = _RecSession([_rec("AAA", 1), _rec("BBB", 2)])
    out = asyncio.run(cycles.run_entries(db, _acct(), date(2026, 7, 30),
                                         quote_fn=lambda s: quotes[s], now=NOW))
    assert out == ["AAA"]
    assert booked == ["AAA"]


def test_run_entries_respects_stock_slots(monkeypatch):
    async def fake_positions(db, account_id):
        # 3 open stock lots at $2k equity -> ladder full
        return [SimPosition(id=uuid4(), account_id=account_id, symbol=s, status="open",
                            shares=_dec(1), avg_cost=_dec(100), stop=_dec(95),
                            r_unit=_dec(5), high_water=_dec(100),
                            entry_date=date(2026, 7, 1), adds_done=1,
                            reversal_count=0, bars_held=1)
                for s in ("S1", "S2", "S3")]

    called = []

    async def fake_open(db, account, **kw):
        called.append(kw["symbol"])
        return object()

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    db = _RecSession([_rec("NEW", 1)])
    out = asyncio.run(cycles.run_entries(db, _acct(cash=500), date(2026, 7, 30),
                                         quote_fn=lambda s: _q(100.0), now=NOW))
    assert out == [] and called == []       # no free stock slot -> nothing booked


def test_check_stops_closes_on_breach(monkeypatch):
    pos = SimPosition(id=uuid4(), account_id=uuid4(), symbol="AAA", status="open",
                      shares=_dec(5), avg_cost=_dec(100), stop=_dec(95),
                      r_unit=_dec(5), high_water=_dec(100),
                      entry_date=date(2026, 7, 1), adds_done=0,
                      reversal_count=0, bars_held=3)
    closes = []

    async def fake_close(db, account, position, **kw):
        closes.append((position.symbol, kw["reason"], kw["idempotency_key"]))
        return object()

    async def fake_positions(db, account_id):
        return [pos]

    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(fake_close))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    out = asyncio.run(cycles.check_stops(None, _acct(), quote_fn=lambda s: _q(94.0),
                                         now=NOW))
    assert out == ["AAA"]
    assert closes[0][1] == "hard_stop"              # stop below cost
    assert closes[0][2] == f"exit:{pos.id}"         # one close per lot, ever

    # above-cost stop labels as trailing
    pos.stop = _dec(101)
    closes.clear()
    asyncio.run(cycles.check_stops(None, _acct(), quote_fn=lambda s: _q(100.5), now=NOW))
    assert closes[0][1] == "trailing"


def test_check_stops_skips_stale_quote(monkeypatch):
    pos = SimPosition(id=uuid4(), account_id=uuid4(), symbol="AAA", status="open",
                      shares=_dec(5), avg_cost=_dec(100), stop=_dec(95),
                      r_unit=_dec(5), high_water=_dec(100),
                      entry_date=date(2026, 7, 1), adds_done=0,
                      reversal_count=0, bars_held=3)

    async def fake_positions(db, account_id):
        return [pos]

    called = []

    async def fake_close(db, account, position, **kw):
        called.append(position.symbol)

    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(fake_close))
    monkeypatch.setattr(SimLedgerService, "get_open_positions", staticmethod(fake_positions))
    out = asyncio.run(cycles.check_stops(None, _acct(),
                                         quote_fn=lambda s: _q(90.0, age_s=30 * 60),
                                         now=NOW))
    assert out == [] and called == []       # stale quote -> never trade on it
