"""Tests for the central LLM wrapper (app.modules.llm.client).

Standalone — no real DB, no network. The Anthropic client is monkeypatched to a
fake whose messages.create returns a canned usage/message (or raises). Asserts:

  * price_for returns known prices and falls back to (0,0) for unknown models,
    and picks DeepSeek's peak vs off-peak rate off the call time;
  * AI_PROVIDER resolves to the right platform / key / base_url / model, and an
    unsupported value is rejected;
  * a successful call books ONE llm_calls row with correct tokens + USD cost
    computed from the snapshotted per-1M prices, and returns the text;
  * an empty API key for the SELECTED provider SKIPS the call and logs nothing;
  * a provider failure never raises — it books a success=False row + error.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.modules.llm import client as llm_client
from app.modules.llm.client import (
    LLM_PRICES, call_llm, price_for, resolve_provider,
)
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


def _patch_anthropic(monkeypatch, *, message=None, exc=None, captured=None):
    import anthropic

    def _factory(**kw):
        if captured is not None:
            captured.append(kw)
        return _FakeAnthropic(message=message, exc=exc, **kw)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _factory)


def _use_claude(monkeypatch, key="sk-test"):
    monkeypatch.setattr(settings, "AI_PROVIDER", "claude")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", key)


def _use_deepseek(monkeypatch, key="sk-deepseek"):
    monkeypatch.setattr(settings, "AI_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", key)


# ---- price table ----------------------------------------------------------


def test_price_for_known_models():
    assert price_for("anthropic", "claude-haiku-4-5") == (Decimal("1.00"), Decimal("5.00"))
    assert price_for("anthropic", "claude-opus-4-8") == (Decimal("5.00"), Decimal("25.00"))
    assert price_for("anthropic", "claude-sonnet-5") == (Decimal("3.00"), Decimal("15.00"))
    # the config's dated haiku id is priced
    assert ("anthropic", "claude-haiku-4-5-20251001") in LLM_PRICES


def test_price_for_unknown_falls_back_to_zero():
    assert price_for("anthropic", "made-up-model") == (Decimal(0), Decimal(0))


def test_deepseek_peak_offpeak_boundary():
    # peak = Mon-Fri 01:00-04:00 and 06:00-10:00 UTC
    fri_peak = datetime(2026, 9, 11, 9, 59, tzinfo=timezone.utc)
    fri_off = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    sat_off = datetime(2026, 9, 12, 2, 0, tzinfo=timezone.utc)

    assert price_for("deepseek", "deepseek-v4-flash", fri_peak) == (
        Decimal("0.44"), Decimal("1.32"))
    assert price_for("deepseek", "deepseek-v4-flash", fri_off) == (
        Decimal("0.22"), Decimal("0.66"))
    assert price_for("deepseek", "deepseek-v4-flash", sat_off) == (
        Decimal("0.22"), Decimal("0.66"))
    assert price_for("deepseek", "deepseek-v4-pro", fri_peak) == (
        Decimal("1.32"), Decimal("3.96"))
    assert price_for("deepseek", "deepseek-v4-pro", sat_off) == (
        Decimal("0.66"), Decimal("1.98"))
    # anthropic is flat — the timestamp changes nothing
    assert price_for("anthropic", "claude-haiku-4-5", fri_peak) == (
        Decimal("1.00"), Decimal("5.00"))


# ---- provider resolution --------------------------------------------------


def test_resolve_provider_claude(monkeypatch):
    _use_claude(monkeypatch)
    monkeypatch.setattr(settings, "ANTHROPIC_MODEL", "claude-haiku-4-5")
    p = resolve_provider()
    assert p.platform == "anthropic" and p.base_url is None
    assert p.api_key == "sk-test" and p.default_model == "claude-haiku-4-5"


def test_resolve_provider_deepseek(monkeypatch):
    _use_deepseek(monkeypatch)
    p = resolve_provider()
    assert p.platform == "deepseek"
    assert p.api_key == "sk-deepseek"
    assert p.base_url == settings.DEEPSEEK_BASE_URL
    assert p.default_model == settings.DEEPSEEK_MODEL


def test_resolve_provider_rejects_unsupported(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "openai")
    with pytest.raises(ValueError):
        resolve_provider()


# ---- call_llm -------------------------------------------------------------


def test_call_llm_success_books_row_and_costs(monkeypatch):
    _use_claude(monkeypatch)
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
    assert row.platform == "anthropic"
    assert row.unit_price_in == Decimal("1.00") and row.unit_price_out == Decimal("5.00")
    assert row.request_id == "req_test123"


def test_call_llm_skipped_when_no_key(monkeypatch):
    _use_claude(monkeypatch, key="")
    db = _FakeSession()
    result = asyncio.run(call_llm(db, context="unit_test", prompt="why?"))
    assert result.success is False and result.skipped is True
    assert result.call_id is None
    assert db.added == []  # no call made -> nothing logged


def test_call_llm_failure_books_error_row(monkeypatch):
    _use_claude(monkeypatch)
    _patch_anthropic(monkeypatch, exc=RuntimeError("boom"))
    db = _FakeSession()
    result = asyncio.run(call_llm(db, context="unit_test", prompt="why?"))
    assert result.success is False and result.text is None
    assert len(db.added) == 1
    row = db.added[0]
    assert row.success is False and "boom" in row.error


def test_call_llm_deepseek_drives_sdk_with_base_url(monkeypatch):
    _use_deepseek(monkeypatch)
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    captured = []
    _patch_anthropic(monkeypatch, message=_Message(1000, 200, "Uptrend intact."),
                     captured=captured)
    db = _FakeSession()

    result = asyncio.run(call_llm(db, context="unit_test", prompt="why?"))

    assert result.success is True
    assert captured[0]["api_key"] == "sk-deepseek"
    assert captured[0]["base_url"] == "https://api.deepseek.com/anthropic"
    row = db.added[0]
    assert row.platform == "deepseek" and row.model == "deepseek-v4-flash"
    assert row.unit_price_in in (Decimal("0.22"), Decimal("0.44"))


def test_call_llm_skipped_when_selected_provider_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    _use_deepseek(monkeypatch, key="")
    db = _FakeSession()
    result = asyncio.run(call_llm(db, context="unit_test", prompt="why?"))
    assert result.skipped is True and result.success is False
    assert "DEEPSEEK_API_KEY" in result.error
    assert db.added == []


def test_call_llm_skipped_on_unsupported_provider(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "openai")
    db = _FakeSession()
    result = asyncio.run(call_llm(db, context="unit_test", prompt="why?"))
    assert result.skipped is True and result.success is False
    assert db.added == []
