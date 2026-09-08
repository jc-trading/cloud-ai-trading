"""Tests for the market.eod_correction Celery wrapper (方案 Phase 8).

Standalone — no DB, no network. The correction logic itself lives in
quant.data.eod_correction and is covered by quant/tests/test_eod_correction.py
(provider flip, fail-closed threshold, empty pages, completeness, the feed
comparison and the 1hour sync); what is asserted here is the wrapper contract:
a skip stays silent, a fail-closed and a partial day each raise exactly one
Telegram line, and no failure of the module escapes into the worker.
"""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models_registry  # noqa: F401,E402

from app.tasks import quant_tasks
from quant.data import eod_correction as qeod


class _FakeSession:
    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _report(**over) -> qeod.CorrectionReport:
    report = qeod.CorrectionReport(day=date(2026, 9, 4), period_key="2026-09-04",
                                   expected=960, symbols=["AAPL", "MSFT"])
    report.results = [qeod.SymbolResult(symbol="AAPL", status="ok", rows=960,
                                        completeness=1.0),
                      qeod.SymbolResult(symbol="MSFT", status="ok", rows=958,
                                        completeness=0.998)]
    for key, value in over.items():
        setattr(report, key, value)
    return report


@pytest.fixture
def wired(monkeypatch):
    sent: list[str] = []
    beats: list[tuple[str, dict]] = []

    async def _notify(message):
        sent.append(message)

    async def _beat(_db, name, **meta):
        beats.append((name, meta))

    monkeypatch.setattr(quant_tasks, "CeleryAsyncSessionLocal", _FakeSession)
    monkeypatch.setattr(quant_tasks, "_notify", _notify)
    monkeypatch.setattr(quant_tasks, "_beat", _beat)
    return sent, beats


def _patch_run(monkeypatch, result):
    def _run(*_a, **_kw):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(qeod, "run", _run)


def test_skip_is_silent(monkeypatch, wired):
    sent, beats = wired
    _patch_run(monkeypatch, qeod.CorrectionReport(day=None, skipped="no completed session"))

    assert quant_tasks.eod_correction() == "skipped: no completed session"
    assert sent == [] and beats == []


def test_clean_run_beats_without_alerting(monkeypatch, wired):
    sent, beats = wired
    _patch_run(monkeypatch, _report())

    result = quant_tasks.eod_correction()

    assert "corrected=2" in result
    assert sent == []
    name, meta = beats[0]
    assert name == "market_eod_correction"
    assert meta["corrected"] == 2 and meta["rows"] == 1918
    assert "fail_closed" not in meta


def test_fail_closed_alerts_once(monkeypatch, wired):
    sent, beats = wired
    report = _report(fail_closed="SIP fetch failed for 3/10 symbols")
    report.results = [qeod.SymbolResult(symbol=s, status="stale") for s in ("AAPL", "MSFT")]
    _patch_run(monkeypatch, report)

    quant_tasks.eod_correction()

    assert len(sent) == 1
    assert "fail-closed" in sent[0] and "3/10" in sent[0]
    assert beats[0][1]["fail_closed"] == "SIP fetch failed for 3/10 symbols"


def test_partial_day_alerts_with_the_symbols(monkeypatch, wired):
    sent, _ = wired
    report = _report()
    report.results[1] = qeod.SymbolResult(symbol="MSFT", status="partial", rows=300,
                                          completeness=0.31)
    _patch_run(monkeypatch, report)

    quant_tasks.eod_correction()

    assert len(sent) == 1
    assert "MSFT" in sent[0] and "incomplete" in sent[0]


def test_module_failure_never_escapes(monkeypatch, wired):
    sent, beats = wired
    _patch_run(monkeypatch, RuntimeError("psycopg down"))

    assert quant_tasks.eod_correction() == "error"
    assert sent == [] and beats == []
