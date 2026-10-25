"""Watchdog coverage for the nightly 1m watch lists (P5b).

Standalone — no DB, no Telegram. `_alert`, the clock and the session factory
are replaced, so what is asserted is the gating: only on a trading day, from an
hour before the regular open (DST-aware) until the close, and only with an
active 1m group, an intraday_nightly heartbeat that has not finished today's
session raises the alert; otherwise the cooldown is cleared.
"""

import sys
import time as time_module
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.modules.system import watchdog

CHECK = "intraday shortlist missing"
# Monday 2026-10-26 is EDT: the open is 13:30 UTC, the alert window starts 12:30
EDT_DAY = "2026-10-26"
# Monday 2026-11-02 is EST: the open is 14:30 UTC, the alert window starts 13:30
EST_DAY = "2026-11-02"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return _FakeResult(self._results.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def alerts(monkeypatch):
    captured: list[tuple[str, str]] = []

    async def _alert(check, message):
        captured.append((check, message))

    monkeypatch.setattr(watchdog, "_alert", _alert)
    watchdog._last_alert_at.clear()
    return captured


def _wire(monkeypatch, now: datetime, *, groups=1, meta=None, missing=False):
    beat = None if missing else SimpleNamespace(name="intraday_nightly", meta=meta)
    session = _FakeSession([groups, beat])
    monkeypatch.setattr(watchdog, "datetime", SimpleNamespace(now=lambda tz=None: now))
    monkeypatch.setattr(watchdog, "time",
                        SimpleNamespace(time=lambda: now.timestamp(),
                                        monotonic=time_module.monotonic))
    import app.database as database

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)
    return session


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_n1_unfinished_list_an_hour_before_the_open_alerts(monkeypatch, alerts):
    _wire(monkeypatch, _utc(2026, 10, 26, 12, 31), meta={"session": EDT_DAY, "done": False})
    await watchdog._check_intraday_nightly()
    assert [c for c, _ in alerts] == [CHECK]
    assert alerts[0][1].startswith(
        f"1 active 1m group(s) but intraday.nightly has not finished the watch list for "
        f"{EDT_DAY} (heartbeat session {EDT_DAY}, done False)")


@pytest.mark.asyncio
async def test_n2_before_the_window_is_quiet(monkeypatch, alerts):
    watchdog._last_alert_at[CHECK] = 1.0
    session = _wire(monkeypatch, _utc(2026, 10, 26, 12, 29), missing=True)
    await watchdog._check_intraday_nightly()
    assert alerts == []
    assert CHECK not in watchdog._last_alert_at
    assert len(session._results) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("hh,mm,alerted", [(13, 31, True), (13, 29, False)])
async def test_n3_the_window_follows_dst(monkeypatch, alerts, hh, mm, alerted):
    _wire(monkeypatch, _utc(2026, 11, 2, hh, mm), missing=True)
    await watchdog._check_intraday_nightly()
    assert [c for c, _ in alerts] == ([CHECK] if alerted else [])
    if alerted:
        assert f"for {EST_DAY} (heartbeat session None, done None)" in alerts[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("done", [True, False, "true", None])
async def test_n4_only_done_today_is_quiet_and_clears_the_cooldown(monkeypatch, alerts, done):
    watchdog._last_alert_at[CHECK] = 1.0
    _wire(monkeypatch, _utc(2026, 10, 26, 15, 0), meta={"session": EDT_DAY, "done": done})
    await watchdog._check_intraday_nightly()
    if done is True:
        assert alerts == []
        assert CHECK not in watchdog._last_alert_at
    else:
        assert [c for c, _ in alerts] == [CHECK]


@pytest.mark.asyncio
async def test_n5_no_active_1m_group_is_quiet(monkeypatch, alerts):
    _wire(monkeypatch, _utc(2026, 10, 26, 15, 0), groups=0, missing=True)
    await watchdog._check_intraday_nightly()
    assert alerts == []


@pytest.mark.asyncio
async def test_n6_weekend_is_quiet(monkeypatch, alerts):
    session = _wire(monkeypatch, _utc(2026, 10, 24, 15, 0), missing=True)
    await watchdog._check_intraday_nightly()
    assert alerts == []
    assert len(session._results) == 2


@pytest.mark.asyncio
async def test_n7_yesterdays_heartbeat_alerts(monkeypatch, alerts):
    _wire(monkeypatch, _utc(2026, 10, 27, 14, 0), groups=2,
          meta={"session": EDT_DAY, "done": True})
    await watchdog._check_intraday_nightly()
    assert [c for c, _ in alerts] == [CHECK]
    assert ("2 active 1m group(s)" in alerts[0][1]
            and f"(heartbeat session {EDT_DAY}, done True)" in alerts[0][1])


@pytest.mark.asyncio
async def test_after_the_close_is_quiet(monkeypatch, alerts):
    _wire(monkeypatch, _utc(2026, 10, 26, 20, 0), missing=True)
    await watchdog._check_intraday_nightly()
    assert alerts == []


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_run_watchdog_runs_the_check(monkeypatch):
    called: list[str] = []

    def _recorder(name):
        async def _check():
            called.append(name)
        return _check

    for name in ("_check_queue_depth", "_check_quant_heartbeats", "_check_market_stream",
                 "_check_stream_rejected", "_check_intraday_nightly", "_check_sim_stops"):
        monkeypatch.setattr(watchdog, name, _recorder(name))

    async def _sleep(_seconds):
        raise _Stop

    monkeypatch.setattr(watchdog, "asyncio", SimpleNamespace(sleep=_sleep))
    with pytest.raises(_Stop):
        await watchdog.run_watchdog()
    assert called.count("_check_intraday_nightly") == 1
