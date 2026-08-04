"""Tests for the recommendation explanation layer (app.modules.llm.explain).

Standalone — no DB, no network. call_llm is monkeypatched. Asserts:

  * each returned recommendation gets its llm_explanation set from the LLM text,
    and one call is made per row with the descriptive system prompt + symbol;
  * a failed/empty LLM result leaves llm_explanation null (None-safe);
  * build_prompt renders the key fields and the watch-only wording, and the
    descriptive-phase guard is in the system prompt.
"""

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.llm import explain as explain_mod
from app.modules.llm.client import LLMResult
from app.modules.llm.explain import _SYSTEM, build_prompt, explain_recommendations


class _Rec:
    def __init__(self, symbol, confidence, *, phase="up", phase_reason="MA rising",
                 direction="up", shortlist_rank=None, features=None):
        self.symbol = symbol
        self.confidence = confidence
        self.phase = phase
        self.phase_reason = phase_reason
        self.direction = direction
        self.shortlist_rank = shortlist_rank
        self.features = features or {"sector": "Technology", "atr_pct": 0.021,
                                     "above_rising_ma20": True, "expected_move": 3.4}
        self.llm_explanation = None


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _Result(self._rows)


def _patch_call(monkeypatch, *, text):
    calls = []

    async def fake_call_llm(db, **kwargs):
        calls.append(kwargs)
        return LLMResult(text=text, call_id=uuid4() if text else None,
                         input_tokens=100, output_tokens=20,
                         cost_usd=Decimal("0.0002"), success=bool(text),
                         error=None if text else "boom")

    monkeypatch.setattr(explain_mod, "call_llm", fake_call_llm)
    return calls


# ---- build_prompt ---------------------------------------------------------


def test_build_prompt_renders_fields_and_shortlist():
    rec = _Rec("NVDA", 92.0, shortlist_rank=1)
    p = build_prompt(rec)
    assert "Symbol: NVDA" in p
    assert "Trend phase: up (MA rising)" in p
    assert "Confidence score: 92.0 / 100" in p
    assert "Buy-shortlist rank: 1" in p
    assert "Sector: Technology" in p


def test_build_prompt_watch_only_when_not_shortlisted():
    rec = _Rec("KO", 60.0, shortlist_rank=None)
    assert "watch-only" in build_prompt(rec)


def test_system_prompt_enforces_descriptive_phase():
    # the down-phase-is-not-a-prediction guard (R0-9 §3.2) must be present
    assert "does not mean the price will fall" in _SYSTEM
    assert "NOT recommending buy or sell" in _SYSTEM


# ---- explain_recommendations ----------------------------------------------


def test_explains_each_row_and_records_calls(monkeypatch):
    rows = [_Rec("NVDA", 92.0, shortlist_rank=1),
            _Rec("KO", 88.0, shortlist_rank=2),
            _Rec("BKR", 70.0)]
    calls = _patch_call(monkeypatch, text="Uptrend, low volatility.")
    db = _FakeSession(rows)

    n = asyncio.run(explain_recommendations(db, date(2026, 8, 5), top_n=10))

    assert n == 3
    assert all(r.llm_explanation == "Uptrend, low volatility." for r in rows)
    assert len(calls) == 3
    assert {c["symbol"] for c in calls} == {"NVDA", "KO", "BKR"}
    assert all(c["system"] == _SYSTEM for c in calls)
    assert all(c["context"] == "recommendation_explanation" for c in calls)


def test_none_safe_when_llm_returns_no_text(monkeypatch):
    rows = [_Rec("NVDA", 92.0, shortlist_rank=1)]
    _patch_call(monkeypatch, text=None)  # simulate provider failure
    db = _FakeSession(rows)

    n = asyncio.run(explain_recommendations(db, date(2026, 8, 5)))

    assert n == 0
    assert rows[0].llm_explanation is None
