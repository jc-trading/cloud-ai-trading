"""Phase A correctness fixes — one pinned regression per finding.

#1  real sectors reach the funnel (the cap no longer collapses the shortlist)
#2  recommendations are never built on bars older than the session
#3  a lot entered today is not marked against the full day's OHLC
#5  a lot exited today cannot re-enter, and slots count the session-open snapshot
#6  the stagnation hurdle is measured against SPY, as the backtest does
#7  a lot whose data died is force-closed at its last known close
#8  alert cooldown follows a SUCCESSFUL send; a missing worker beat is stale
#10 a symbol whose prices are RAW for want of a corporate action is excluded
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import app.models_registry  # noqa: F401  (mapper config for SimPosition)

from app.modules.notifications import telegram as telegram_mod
from app.modules.simledger import cycles
from app.modules.simledger.models import Recommendation, SimAccount, SimPosition
from app.modules.simledger.service import SimLedgerService, _dec
from app.modules.system import watchdog
from quant.data import bars as qbars

SESSION = date(2026, 7, 30)
NOW = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)


def _acct(cash=2000.0):
    return SimAccount(id=uuid4(), user_id=uuid4(), name="default", is_system=True,
                      starting_capital=_dec(2000), cash=_dec(cash))


def _q(price, age_s=0):
    return cycles.QuoteReading(price=price, at=NOW - timedelta(seconds=age_s))


def _frame(closes, *, last_day=SESSION, vol=50_000_000):
    n = len(closes)
    ts = pd.DatetimeIndex([pd.Timestamp(last_day, tz="America/New_York")
                           - pd.Timedelta(days=n - 1 - i)
                           for i in range(n)]).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes], "close": closes,
        "volume": [vol] * n, "vwap": closes, "trade_count": [100] * n,
    })


def _uptrend(slope):
    return _frame([50 + i * slope for i in range(120)])


def _pos(symbol="AAA", *, entry_date=date(2026, 6, 30), avg_cost=50.0, stop=10.0,
         r_unit=1000.0, bars_held=3, high_water=50.0, reversal_count=0):
    return SimPosition(id=uuid4(), account_id=uuid4(), symbol=symbol, status="open",
                       shares=_dec(5), avg_cost=_dec(avg_cost), stop=_dec(stop),
                       r_unit=_dec(r_unit), high_water=_dec(high_water),
                       entry_date=entry_date, adds_done=0,
                       reversal_count=reversal_count, bars_held=bars_held)


def _positions(*positions):
    async def fake(db, account_id):
        return list(positions)
    return staticmethod(fake)


# --- #1: real sectors must reach the funnel ---------------------------------

_LOOSE = cycles.qfunnel.FunnelParams(min_confidence=0.0, atr_pct_min=0.0)


def _shortlist(sectors):
    frames = {s: _uptrend(0.8 - i * 0.05) for i, s in enumerate(sectors)}
    batch = cycles.build_recommendations(
        list(sectors), SESSION, funnel_params=_LOOSE,
        bars_fn=lambda s, tf, end: frames[s], sectors=sectors)
    return [r["symbol"] for r in batch.rows if r["shortlist_rank"]]


def test_shortlist_spans_three_sectors():
    """The P0: with every name scored as 'unknown' the sector cap kept 2."""
    picked = _shortlist({"AAA": "tech", "BBB": "energy", "CCC": "health"})
    assert len(picked) == 3
    assert sorted(picked) == ["AAA", "BBB", "CCC"]


def test_shortlist_capped_at_two_within_one_sector():
    picked = _shortlist({"AAA": "tech", "BBB": "tech", "CCC": "tech"})
    assert len(picked) == cycles.qfunnel.FunnelParams().max_per_sector == 2


def test_sector_defaults_to_unknown_when_not_supplied():
    frames = {"AAA": _uptrend(0.8)}
    batch = cycles.build_recommendations(["AAA"], SESSION, funnel_params=_LOOSE,
                                         bars_fn=lambda s, tf, end: frames[s])
    assert batch.rows[0]["features"]["sector"] == "unknown"


# --- #2: recommendations are never built on stale bars ----------------------

def test_build_recommendations_skips_stale_symbol(caplog):
    frames = {"FRESH": _uptrend(0.8),
              "STALE": _frame([50 + i * 0.8 for i in range(120)],
                              last_day=date(2026, 7, 29))}
    batch = cycles.build_recommendations(
        ["FRESH", "STALE"], SESSION, funnel_params=_LOOSE,
        bars_fn=lambda s, tf, end: frames[s],
        sectors={"FRESH": "tech", "STALE": "energy"})
    assert [r["symbol"] for r in batch.rows] == ["FRESH"]
    assert (batch.scanned, batch.stale, batch.unadjusted) == (2, 1, 0)
    assert batch.excluded == 1
    assert "excluded 1 symbols on stale bars" in caplog.text


# --- #10: unadjusted prices fail closed per symbol --------------------------

def test_build_recommendations_excludes_unadjusted_symbol(caplog):
    frames = {"GOOD": _uptrend(0.8), "SPLIT": _uptrend(0.7)}
    frames["SPLIT"].attrs["unadjusted"] = True

    batch = cycles.build_recommendations(
        ["GOOD", "SPLIT"], SESSION, funnel_params=_LOOSE,
        bars_fn=lambda s, tf, end: frames[s],
        sectors={"GOOD": "tech", "SPLIT": "energy"})
    assert [r["symbol"] for r in batch.rows] == ["GOOD"]
    assert (batch.scanned, batch.stale, batch.unadjusted) == (2, 0, 1)
    assert "and 1 on unadjusted prices" in caplog.text


# --- #3: the entry bar is not folded on the entry day -----------------------

def test_daily_exit_does_not_mark_entry_day_position(monkeypatch):
    """The whole day's low includes prints from before the intraday fill."""
    pos = _pos(entry_date=SESSION, stop=200.0, bars_held=0)   # stop above the bar
    monkeypatch.setattr(SimLedgerService, "get_open_positions", _positions(pos))
    closes = []

    async def fake_close(db, account, position, **kw):
        closes.append(kw["reason"])

    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(fake_close))
    frames = {"AAA": _uptrend(0.8), "SPY": _frame([100.0] * 120)}
    out = asyncio.run(cycles.daily_exit_management(
        None, _acct(), SESSION, bars_fn=lambda s, tf, end: frames[s]))
    assert out.closed == [] and out.data_end == [] and closes == []
    assert pos.bars_held == 0                    # bar never folded


def test_daily_exit_still_marks_a_position_entered_earlier(monkeypatch):
    pos = _pos(entry_date=date(2026, 7, 29), stop=200.0, bars_held=0)
    monkeypatch.setattr(SimLedgerService, "get_open_positions", _positions(pos))
    closes = []

    async def fake_close(db, account, position, **kw):
        closes.append(kw["reason"])

    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(fake_close))
    frames = {"AAA": _uptrend(0.8), "SPY": _frame([100.0] * 120)}
    out = asyncio.run(cycles.daily_exit_management(
        None, _acct(), SESSION, bars_fn=lambda s, tf, end: frames[s]))
    assert out.closed == ["AAA"] and closes == ["trailing"]  # stop above the bar


# --- #6: the stagnation hurdle is SPY-relative ------------------------------

_FADING = [50 + i * 0.8 for i in range(100)] + [130 - i * 0.35 for i in range(40)]


def _stagnation_run(monkeypatch, spy_last_close):
    pos = _pos(bars_held=30)
    monkeypatch.setattr(SimLedgerService, "get_open_positions", _positions(pos))

    async def fake_close(db, account, position, **kw):
        return object()

    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(fake_close))
    spy = _frame([100.0] * 119 + [spy_last_close])
    frames = {"AAA": _frame(_FADING), "SPY": spy}
    return asyncio.run(cycles.daily_exit_management(
        None, _acct(), SESSION, bars_fn=lambda s, tf, end: frames[s])).closed


def test_stagnation_fires_when_lagging_spy(monkeypatch):
    # SPY +300% since entry vs the lot's +133% -> below the hurdle
    assert _stagnation_run(monkeypatch, 400.0) == ["AAA"]


def test_stagnation_held_when_beating_spy(monkeypatch):
    # SPY +10% since entry vs the lot's +133% -> the hurdle is cleared, so the
    # lot survives a day it would have been sold on without a benchmark
    assert _stagnation_run(monkeypatch, 110.0) == []


# --- #7: data_end force-close -----------------------------------------------

def _data_end_run(monkeypatch, last_bar_day):
    pos = _pos()
    monkeypatch.setattr(SimLedgerService, "get_open_positions", _positions(pos))
    booked = []

    async def fake_close(db, account, position, **kw):
        booked.append((position.symbol, kw["reason"], kw["raw_price"]))
        return object()

    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(fake_close))
    frames = {"AAA": _frame([50 + i * 0.8 for i in range(120)], last_day=last_bar_day),
              "SPY": _frame([100.0] * 120)}
    out = asyncio.run(cycles.daily_exit_management(
        None, _acct(), SESSION, bars_fn=lambda s, tf, end: frames[s]))
    return out.closed, out.data_end, booked


def test_data_end_closes_after_n_missing_sessions(monkeypatch):
    # last bar Friday 07-24; 07-27..07-30 are four sessions with no new data
    closed, data_end, booked = _data_end_run(monkeypatch, date(2026, 7, 24))
    assert closed == ["AAA"] and data_end == ["AAA"]
    assert booked[0][1] == "data_end"
    assert booked[0][2] == pytest.approx(50 + 119 * 0.8)      # last known close


def test_data_end_holds_below_the_threshold(monkeypatch):
    # last bar 07-28 -> only 07-29 and 07-30 missing (< 3): stale, not dead
    closed, data_end, booked = _data_end_run(monkeypatch, date(2026, 7, 28))
    assert closed == [] and data_end == [] and booked == []


# --- #5: today's exit frees nothing -----------------------------------------

def _rec(symbol, rank=1):
    return Recommendation(id=uuid4(), symbol=symbol, trade_date=SESSION,
                          direction="up", confidence=Decimal("70"),
                          shortlist_rank=rank, phase="up", phase_reason="",
                          features={"stop_distance": 2.0, "adv": 5e7, "price": 100.0})


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


def _entries(monkeypatch, recs, *, open_positions=(), closed_today=()):
    booked = []

    async def fake_open(db, account, **kw):
        booked.append(kw["symbol"])
        return object()

    async def fake_closed_on(db, account_id, session_date):
        assert session_date == SESSION
        return list(closed_today)

    monkeypatch.setattr(SimLedgerService, "open_or_add", staticmethod(fake_open))
    monkeypatch.setattr(SimLedgerService, "get_open_positions",
                        _positions(*open_positions))
    monkeypatch.setattr(SimLedgerService, "get_positions_closed_on",
                        staticmethod(fake_closed_on))
    out = asyncio.run(cycles.run_entries(_RecSession(recs), _acct(), SESSION,
                                         quote_fn=lambda s: _q(100.0), now=NOW))
    return out, booked


def test_no_reentry_after_todays_exit(monkeypatch):
    gone = _pos("AAA", entry_date=date(2026, 7, 1))
    gone.status = "closed"
    out, booked = _entries(monkeypatch, [_rec("AAA")], closed_today=[gone])
    assert out == [] and booked == []


def test_slots_count_the_session_open_snapshot(monkeypatch):
    """Two lots still open plus one stopped out this morning = the ladder was
    full at the open; an intraday exit must not hand its slot to a new name."""
    held = [_pos("S1", entry_date=date(2026, 7, 1)),
            _pos("S2", entry_date=date(2026, 7, 1))]
    gone = _pos("S3", entry_date=date(2026, 7, 1))
    gone.status = "closed"
    out, booked = _entries(monkeypatch, [_rec("NEW")], open_positions=held,
                           closed_today=[gone])
    assert out == [] and booked == []

    # same day, without the morning exit: the third slot is genuinely free
    out, booked = _entries(monkeypatch, [_rec("NEW")], open_positions=held)
    assert out == ["NEW"] and booked == ["NEW"]


# --- #8: the alert chain ----------------------------------------------------

class _Notifier:
    """TelegramNotifier stand-in whose send_message returns queued outcomes."""

    def __init__(self, outcomes, sends):
        self._outcomes, self._sends = outcomes, sends

    async def send_message(self, message, parse_mode=None):
        self._sends.append(message)
        out = self._outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def alert_chain(monkeypatch):
    sends, outcomes = [], []
    monkeypatch.setattr(watchdog, "TelegramNotifier",
                        lambda *a, **kw: _Notifier(outcomes, sends))
    watchdog._last_alert_at.clear()
    return SimpleNamespace(sends=sends, outcomes=outcomes)


@pytest.mark.asyncio
async def test_cooldown_only_starts_after_a_successful_send(alert_chain):
    alert_chain.outcomes.extend([False, RuntimeError("dns"), True, True])
    for _ in range(4):
        await watchdog._alert("check", "body")
    # 3 attempts got through (fail, raise, success); the 4th hit the cooldown
    assert len(alert_chain.sends) == 3
    assert watchdog._last_alert_at["check"] is not None


@pytest.mark.asyncio
async def test_successful_send_suppresses_the_next_alert(alert_chain):
    alert_chain.outcomes.extend([True, True])
    await watchdog._alert("check", "body")
    await watchdog._alert("check", "body")
    assert len(alert_chain.sends) == 1


class _HeartbeatSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        rows = self._rows

        class _R:
            def scalars(self):
                class _S:
                    def all(self):
                        return rows
                return _S()
        return _R()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _wire_heartbeats(monkeypatch, rows, now_et):
    import app.database as database

    monkeypatch.setattr(database, "AsyncSessionLocal",
                        lambda: _HeartbeatSession(rows))
    monkeypatch.setattr(cycles, "now_et", lambda: now_et)


@pytest.fixture
def alerts(monkeypatch):
    captured = []

    async def _alert(check, message):
        captured.append((check, message))

    monkeypatch.setattr(watchdog, "_alert", _alert)
    watchdog._last_alert_at.clear()
    return captured


# Tuesday 2026-09-08 10:00 ET — mid-session; Saturday 09-05 is not a session.
_ET = ZoneInfo("America/New_York")
IN_RTH = datetime(2026, 9, 8, 10, 0, tzinfo=_ET)
WEEKEND = datetime(2026, 9, 5, 10, 0, tzinfo=_ET)


@pytest.mark.asyncio
async def test_missing_worker_heartbeat_alerts_in_session(monkeypatch, alerts):
    """A heartbeat row that was NEVER written is not evidence of health."""
    _wire_heartbeats(monkeypatch, [], IN_RTH)
    await watchdog._check_quant_heartbeats()
    assert "worker heartbeat stale" in [c for c, _ in alerts]
    assert "NEVER" in dict(alerts)["worker heartbeat stale"]


@pytest.mark.asyncio
async def test_missing_worker_heartbeat_is_quiet_outside_the_session(monkeypatch, alerts):
    _wire_heartbeats(monkeypatch, [], WEEKEND)
    await watchdog._check_quant_heartbeats()
    assert [c for c, _ in alerts] == []


class _FakeResp:
    def __init__(self, status):
        self.status = status

    async def text(self):
        return "boom"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def telegram_transport(monkeypatch):
    state = SimpleNamespace(outcomes=[], posts=[], sleeps=[])

    class _Session:
        def post(self, url, **kw):
            state.posts.append(kw.get("json"))
            out = state.outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return _FakeResp(out)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(telegram_mod.aiohttp, "ClientSession", lambda *a, **kw: _Session())
    monkeypatch.setattr(telegram_mod.aiohttp, "ClientTimeout", lambda **kw: None)

    async def fake_sleep(s):
        state.sleeps.append(s)

    monkeypatch.setattr(telegram_mod.asyncio, "sleep", fake_sleep)
    return state


@pytest.mark.asyncio
async def test_send_message_retries_until_it_succeeds(telegram_transport):
    telegram_transport.outcomes.extend([500, RuntimeError("dns"), 200])
    notifier = telegram_mod.TelegramNotifier(bot_token="t", chat_id="c")
    assert await notifier.send_message("hi", parse_mode=None) is True
    assert len(telegram_transport.posts) == 3
    assert telegram_transport.sleeps == [1, 5]        # stopped once it landed


@pytest.mark.asyncio
async def test_send_message_sleeps_the_whole_backoff_then_gives_up(telegram_transport):
    telegram_transport.outcomes.extend([500] * telegram_mod.SEND_ATTEMPTS)
    notifier = telegram_mod.TelegramNotifier(bot_token="t", chat_id="c")
    assert await notifier.send_message("hi", parse_mode=None) is False
    # every backoff step is actually slept — the 30s used to be dead config
    assert len(telegram_transport.posts) == 4 == telegram_mod.SEND_ATTEMPTS
    assert telegram_transport.sleeps == [1, 5, 30]    # no sleep after the last
    assert telegram_transport.posts[0] == {"chat_id": "c", "text": "hi"}


# --- QA 1: a night that publishes nothing must be LOUD ----------------------

def test_batch_counts_reach_the_caller():
    """The counters are the alert's input; logging them was not enough."""
    frames = {"FRESH": _uptrend(0.8),
              "STALE": _frame([50 + i * 0.8 for i in range(120)],
                              last_day=date(2026, 7, 29)),
              "SPLIT": _uptrend(0.7)}
    frames["SPLIT"].attrs["unadjusted"] = True
    batch = cycles.build_recommendations(
        ["FRESH", "STALE", "SPLIT"], SESSION, funnel_params=_LOOSE,
        bars_fn=lambda s, tf, end: frames[s])
    assert (batch.scanned, batch.stale, batch.unadjusted) == (3, 1, 1)
    assert batch.excluded == 2
    assert [r["symbol"] for r in batch.rows] == ["FRESH"]


def test_empty_batch_still_reports_what_it_scanned():
    frames = {"STALE": _frame([50 + i * 0.8 for i in range(120)],
                              last_day=date(2026, 7, 29))}
    batch = cycles.build_recommendations(["STALE"], SESSION, funnel_params=_LOOSE,
                                         bars_fn=lambda s, tf, end: frames[s])
    assert batch.rows == [] and batch.scanned == 1 and batch.stale == 1


# --- QA 2: the RAW guard must be clearable ----------------------------------

def _hood_like(jump_at: int, n: int = 200):
    closes = [50.0 + i * 0.05 for i in range(n)]
    for i in range(jump_at, n):
        closes[i] += 40.0                       # a +~50% single-day step
    return _frame(closes)


def test_synced_symbol_with_zero_actions_is_not_flagged():
    """HOOD: a real 2021 move + no provider actions used to exclude it forever,
    because a genuinely action-free name can never cache the clearing action."""
    df = _hood_like(jump_at=20)                 # the jump is ancient history
    assert qbars.is_unadjusted("HOOD", df, actions=None,
                               synced_at=date(2026, 7, 30)) is False


def test_jump_after_the_last_sync_is_still_flagged():
    df = _hood_like(jump_at=len(_hood_like(20)) - 3)
    assert qbars.is_unadjusted("HOOD", df, actions=None,
                               synced_at=date(2026, 7, 20)) is True


def test_never_synced_symbol_only_scans_recent_bars():
    old_jump = _hood_like(jump_at=20)
    recent_jump = _hood_like(jump_at=197)
    assert qbars.is_unadjusted("NEW", old_jump) is False        # 2021-style move
    assert qbars.is_unadjusted("NEW", recent_jump) is True      # unsynced split


def test_cached_action_clears_the_flag():
    actions = pd.DataFrame({"symbol": ["X"], "ex_date": [date(2026, 7, 20)],
                            "action_type": ["split"], "ratio": [2.0],
                            "cash_amount": [None]})
    assert qbars.is_unadjusted("X", _hood_like(jump_at=197), actions=actions) is False


# --- QA 3: the exit path fails closed on RAW prices too ---------------------

def test_both_paths_fail_closed_on_one_memoized_bars_fn(monkeypatch):
    """One memo feeds recommendations AND the exit pass: the RAW signal must
    survive the cache, which a warnings-based signal cannot."""
    split = _frame([100.0] * 119 + [50.0])      # an unsynced 2:1 split
    split.attrs["unadjusted"] = True
    frames = {"AAA": split, "SPY": _frame([100.0] * 120)}
    reads = []

    def get_bars(symbol, timeframe="1d", *, end=None):
        reads.append(symbol)
        return frames[symbol]

    bars_fn = cycles.memoized_bars_fn(SESSION, get_bars=get_bars)

    batch = cycles.build_recommendations(["AAA"], SESSION, funnel_params=_LOOSE,
                                         bars_fn=bars_fn)
    assert batch.rows == [] and batch.unadjusted == 1

    pos = _pos("AAA", avg_cost=100.0, stop=95.0, r_unit=5.0, high_water=100.0)
    monkeypatch.setattr(SimLedgerService, "get_open_positions", _positions(pos))
    closes = []

    async def fake_close(db, account, position, **kw):
        closes.append(kw["reason"])

    monkeypatch.setattr(SimLedgerService, "close_position", staticmethod(fake_close))
    out = asyncio.run(cycles.daily_exit_management(
        None, _acct(), SESSION, bars_fn=bars_fn))

    # the -50% "day" would have booked a hard_stop at half price
    assert closes == [] and out.closed == [] and out.unadjusted == ["AAA"]
    assert pos.bars_held == 3                    # untouched
    assert reads.count("AAA") == 1               # the memo served the exit pass


# --- #11: the RTH gate follows the calendar, not a hardcoded 16:00 ----------

# 2025-11-28 is the Friday after Thanksgiving — a 13:00 ET early close.
EARLY_CLOSE_DAY = date(2025, 11, 28)
NORMAL_DAY = date(2026, 8, 18)


def _et(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=_ET)


def test_rth_gate_ends_at_the_early_close():
    """A half day used to keep booking entries and running stop checks for three
    hours after the tape stopped."""
    assert cycles.in_rth(_et(EARLY_CLOSE_DAY, 12, 30)) is True
    assert cycles.in_rth(_et(EARLY_CLOSE_DAY, 13, 30)) is False


def test_rth_gate_spans_the_full_normal_session():
    assert cycles.in_rth(_et(NORMAL_DAY, 9, 29)) is False
    assert cycles.in_rth(_et(NORMAL_DAY, 9, 30)) is True
    assert cycles.in_rth(_et(NORMAL_DAY, 15, 59)) is True
    assert cycles.in_rth(_et(NORMAL_DAY, 16, 0)) is False


def test_rth_gate_open_grace_and_non_sessions():
    assert cycles.in_rth(_et(NORMAL_DAY, 9, 35), open_grace_min=10) is False
    assert cycles.in_rth(_et(NORMAL_DAY, 9, 41), open_grace_min=10) is True
    assert cycles.in_rth(_et(date(2026, 8, 22), 12, 0)) is False       # Saturday
    # a naive datetime is still read as ET (the pre-calendar call shape)
    assert cycles.in_rth(datetime(2025, 11, 28, 12, 30)) is True
