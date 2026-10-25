"""Watchdog coverage for symbols the IEX stream refused (P5b).

Standalone — no DB, no Telegram. `_alert` and the session factory are replaced,
so what is asserted is the gating: only a live market_stream heartbeat is
judged, and either rejected names or an effective cap below the configured one
raises the alert; a clean heartbeat clears its cooldown.
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

NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
CHECK = "stream rejected symbols"


class _FakeResult:
    def __init__(self, value):
        self._value = value

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


def _wire(monkeypatch, *, meta=None, age_s=10, missing=False):
    beat = None if missing else SimpleNamespace(
        name="market_stream", meta=meta, last_beat_at=NOW - timedelta(seconds=age_s))
    monkeypatch.setattr(watchdog, "time",
                        SimpleNamespace(time=lambda: NOW.timestamp(),
                                        monotonic=time_module.monotonic))
    import app.database as database

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _FakeSession([beat]))


@pytest.mark.asyncio
async def test_rejected_symbols_alert(monkeypatch, alerts):
    _wire(monkeypatch, meta={"rejected": ["BBB", "AAA"], "cap_effective": 30})
    await watchdog._check_stream_rejected()
    assert [c for c, _ in alerts] == [CHECK]
    assert "rejected 2: BBB, AAA" in alerts[0][1]
    assert "cap_effective 30 (configured 30)" in alerts[0][1]


@pytest.mark.asyncio
async def test_lowered_cap_alerts(monkeypatch, alerts):
    _wire(monkeypatch, meta={"rejected": [], "cap_effective": 29})
    await watchdog._check_stream_rejected()
    assert [c for c, _ in alerts] == [CHECK]
    assert "cap_effective 29 (configured 30); rejected 0: -" in alerts[0][1]


@pytest.mark.asyncio
async def test_clean_heartbeat_clears_the_cooldown(monkeypatch, alerts):
    watchdog._last_alert_at[CHECK] = 1.0
    _wire(monkeypatch, meta={"rejected": [], "cap_effective": 30})
    await watchdog._check_stream_rejected()
    assert alerts == []
    assert CHECK not in watchdog._last_alert_at


@pytest.mark.asyncio
async def test_stale_heartbeat_is_not_judged(monkeypatch, alerts):
    watchdog._last_alert_at[CHECK] = 1.0
    _wire(monkeypatch, meta={"rejected": ["AAA"], "cap_effective": 29},
          age_s=watchdog.MARKET_STREAM_STALE + 1)
    await watchdog._check_stream_rejected()
    assert alerts == []
    assert watchdog._last_alert_at[CHECK] == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"missing": True}, {"meta": None}])
async def test_missing_heartbeat_or_meta_is_quiet(monkeypatch, alerts, kw):
    _wire(monkeypatch, **kw)
    await watchdog._check_stream_rejected()
    assert alerts == []


class _Stop(Exception):
    pass


@pytest.mark.asyncio
async def test_run_watchdog_runs_the_check_after_market_stream(monkeypatch):
    called: list[str] = []

    def _recorder(name):
        async def _check():
            called.append(name)
        return _check

    for name in ("_check_queue_depth", "_check_quant_heartbeats", "_check_market_stream",
                 "_check_stream_rejected", "_check_sim_stops"):
        monkeypatch.setattr(watchdog, name, _recorder(name))

    async def _sleep(_seconds):
        raise _Stop

    monkeypatch.setattr(watchdog, "asyncio", SimpleNamespace(sleep=_sleep))
    with pytest.raises(_Stop):
        await watchdog.run_watchdog()
    assert called.count("_check_stream_rejected") == 1
    assert called.index("_check_market_stream") < called.index("_check_stream_rejected")
