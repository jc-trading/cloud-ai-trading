"""Watchdog coverage for the realtime bar writer (方案 Phase 8).

Standalone — no DB, no Telegram. `_alert` and the session factory are replaced,
so what is asserted is the gating: the market_stream heartbeat is only judged
inside the 04:00-20:00 ET session window of a trading day, and the previous
session's uncorrected 1min files raise one summary line.
"""

import sys
import time as time_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.modules.system import watchdog

# Tuesday 2026-09-08 10:00 ET — mid-session; the previous session is Friday
# 2026-09-04 (Labor Day Monday is not one).
IN_SESSION = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)
AFTER_CLOSE = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)   # 21:00 ET Tuesday
HOLIDAY = datetime(2026, 9, 7, 17, 0, tzinfo=timezone.utc)      # 13:00 ET Labor Day
# the run due for session 2026-09-04 fires 2026-09-05 01:30 UTC
EOD_RAN = datetime(2026, 9, 5, 1, 31, tzinfo=timezone.utc)
NEVER = EOD_RAN  # default for tests that are not about the EOD heartbeat


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
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


def _wire(monkeypatch, now: datetime, *, beat_age_s=None, bad_rows=(),
          eod_beat_at=NEVER):
    beat = None
    if beat_age_s is not None:
        beat = SimpleNamespace(name="market_stream",
                               last_beat_at=now - timedelta(seconds=beat_age_s))
    eod_beat = (None if eod_beat_at is None
                else SimpleNamespace(name="market_eod_correction",
                                     last_beat_at=eod_beat_at))
    session = _FakeSession([beat, list(bad_rows), eod_beat])
    monkeypatch.setattr(watchdog, "datetime",
                        SimpleNamespace(now=lambda tz=None: now))
    monkeypatch.setattr(watchdog, "time",
                        SimpleNamespace(time=lambda: now.timestamp(),
                                        monotonic=time_module.monotonic))
    import app.database as database

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: session)


@pytest.mark.asyncio
async def test_stale_heartbeat_in_session_alerts(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=10 * 60)
    await watchdog._check_market_stream()
    assert [c for c, _ in alerts] == ["market stream stale"]
    assert "10m old" in alerts[0][1]


@pytest.mark.asyncio
async def test_missing_heartbeat_in_session_alerts(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=None)
    await watchdog._check_market_stream()
    assert [c for c, _ in alerts] == ["market stream stale"]
    assert "missing" in alerts[0][1]


@pytest.mark.asyncio
async def test_fresh_heartbeat_is_quiet(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=60)
    await watchdog._check_market_stream()
    assert alerts == []


@pytest.mark.asyncio
async def test_outside_the_session_window_is_quiet(monkeypatch, alerts):
    _wire(monkeypatch, AFTER_CLOSE, beat_age_s=6 * 3600)
    await watchdog._check_market_stream()
    assert alerts == []


@pytest.mark.asyncio
async def test_non_trading_day_is_quiet(monkeypatch, alerts):
    _wire(monkeypatch, HOLIDAY, beat_age_s=6 * 3600)
    await watchdog._check_market_stream()
    assert alerts == []


@pytest.mark.asyncio
async def test_uncorrected_files_raise_one_summary(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=60,
          bad_rows=[("AAPL", "stale", "alpaca:sip"),
                    ("MSFT", "partial", "alpaca:sip")])
    await watchdog._check_market_stream()
    assert [c for c, _ in alerts] == ["eod correction incomplete"]
    message = alerts[0][1]
    assert "2026-09-04" in message
    assert "AAPL (stale)" in message and "MSFT (partial)" in message


@pytest.mark.asyncio
async def test_uncorrected_provider_is_flagged_even_when_status_is_ok(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=60,
          bad_rows=[("AAPL", "ok", "alpaca:iex")])
    await watchdog._check_market_stream()
    assert [c for c, _ in alerts] == ["eod correction incomplete"]
    assert "AAPL (alpaca:iex)" in alerts[0][1]


@pytest.mark.asyncio
async def test_missed_eod_run_alerts(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=60,
          eod_beat_at=datetime(2026, 9, 4, 1, 31, tzinfo=timezone.utc))
    await watchdog._check_market_stream()
    assert [c for c, _ in alerts] == ["eod correction missed"]
    assert "2026-09-04" in alerts[0][1]


@pytest.mark.asyncio
async def test_never_run_eod_alerts(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=60, eod_beat_at=None)
    await watchdog._check_market_stream()
    assert [c for c, _ in alerts] == ["eod correction missed"]
    assert "never" in alerts[0][1]


@pytest.mark.asyncio
async def test_fresh_eod_run_is_quiet(monkeypatch, alerts):
    _wire(monkeypatch, IN_SESSION, beat_age_s=60, eod_beat_at=EOD_RAN)
    await watchdog._check_market_stream()
    assert alerts == []
