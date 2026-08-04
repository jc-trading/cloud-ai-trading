"""Tests for the central LLM wrapper (app.modules.llm.client).

Standalone — no real DB, no network. The Anthropic client is monkeypatched to a
fake whose messages.create returns a canned usage/message (or raises). Asserts:

  * price_for returns known prices and falls back to (0,0) for unknown models;
  * a successful call books ONE llm_calls row with correct tokens + USD cost
    computed from the snapshotted per-1M prices, and returns the text;
  * an empty ANTHROPIC_API_KEY SKIPS the call and logs nothing;
  * a provider failure never raises — it books a success=False row + error.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.modules.llm import client as llm_client
from app.modules.llm.client import LLM_PRICES, call_llm, price_for
from app.modules.llm.models import LLMCall


# ---- fakes ----------------------------------------------------------------


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


class _Usage:
    def __init__(self, in_tok, out_tok):
        self.input_tokens = in_tok
        self.output_tokens = out_tok
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Message:
    def __init__(self, in_tok, out_tok, text):
        self.usage = _Usage(in_tok, out_tok)
        self.content = [_Block(text)]
        self._request_id = "req_test123"


class _FakeMessages:
    def __init__(self, message=None, exc=None):
        self._message = message
        self._exc = exc

    async def create(self, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._message


class _FakeAnthropic:
    def __init__(self, message=None, exc=None, **_kw):
        self.messages = _FakeMessages(message, exc)


def _patch_anthropic(monkeypatch, *, message=None, exc=None):
    import anthropic
    monkeypatch.setattr(
        anthropic, "AsyncAnthropic",
        lambda **kw: _FakeAnthropic(message=message, exc=exc, **kw),
    )


# ---- price table ----------------------------------------------------------


def test_price_for_known_models():
    assert price_for("anthropic", "claude-haiku-4-5") == (Decimal("1.00"), Decimal("5.00"))
    assert price_for("anthropic", "claude-opus-4-8") == (Decimal("5.00"), Decimal("25.00"))
    assert price_for("anthropic", "claude-sonnet-5") == (Decimal("3.00"), Decimal("15.00"))
    # the config's dated haiku id is priced
    assert ("anthropic", "claude-haiku-4-5-20251001") in LLM_PRICES


def test_price_for_unknown_falls_back_to_zero():
    assert price_for("anthropic", "made-up-model") == (Decimal(0), Decimal(0))


# ---- call_llm -------------------------------------------------------------


def test_call_llm_success_books_row_and_costs(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    _patch_anthropic(monkeypatch, message=_Message(1000, 200, "Strong beat, uptrend."))
    db = _FakeSession()

    result = asyncio.run(call_llm(
        db, context="unit_test", prompt="why?", symbol="NVDA",
        model="claude-haiku-4-5",
    ))

    assert result.success is True
    assert result.text == "Strong beat, uptrend."
    assert result.input_tokens == 1000 and result.output_tokens == 200
    # 1000/1e6*$1 + 200/1e6*$5 = 0.001 + 0.001 = 0.002
    assert result.cost_usd == Decimal("0.002000")
    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, LLMCall)
    assert row.success is True and row.symbol == "NVDA"
    assert row.unit_price_in == Decimal("1.00") and row.unit_price_out == Decimal("5.00")
    assert row.request_id == "req_test123"


def test_call_llm_skipped_when_no_key(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    db = _FakeSession()
    result = asyncio.run(call_llm(db, context="unit_test", prompt="why?"))
    assert result.success is False and result.skipped is True
    assert result.call_id is None
    assert db.added == []  # no call made -> nothing logged


def test_call_llm_failure_books_error_row(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    _patch_anthropic(monkeypatch, exc=RuntimeError("boom"))
    db = _FakeSession()
    result = asyncio.run(call_llm(db, context="unit_test", prompt="why?"))
    assert result.success is False and result.text is None
    assert len(db.added) == 1
    row = db.added[0]
    assert row.success is False and "boom" in row.error
