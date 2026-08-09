"""Tests for TelegramNotifier.send_paper_order_fill.

Standalone — no network. ``send_message`` is patched to capture the built text,
so we assert the PAPER disclaimer + symbol / quantity / entry / reasoning are all
present and that missing fields degrade safely instead of raising.
"""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.notifications.telegram import TelegramNotifier, escape_markdown


def _capture():
    notifier = TelegramNotifier(bot_token="t", chat_id="c")
    sent = {}

    async def _fake_send(message):
        sent["message"] = message
        return True

    notifier.send_message = _fake_send
    return notifier, sent


def test_paper_fill_message_has_paper_flag_and_all_fields():
    notifier, sent = _capture()
    ok = asyncio.run(
        notifier.send_paper_order_fill(
            symbol="AAPL",
            quantity=33,
            entry_price=150.25,
            reason="Earnings beat + guidance raise.",
        )
    )
    assert ok is True
    msg = sent["message"]
    assert "PAPER" in msg                       # PAPER identifier present
    assert "AAPL" in msg                        # symbol
    assert "33" in msg                          # quantity
    assert "$150.25" in msg                     # entry price
    assert "Earnings beat + guidance raise." in msg  # reasoning summary


def test_paper_fill_none_safe_missing_fields():
    notifier, sent = _capture()
    # No quantity / entry / reason — must not raise, must still send.
    ok = asyncio.run(
        notifier.send_paper_order_fill(symbol=None, quantity=None, entry_price=None, reason=None)
    )
    assert ok is True
    msg = sent["message"]
    assert "PAPER" in msg
    assert "n/a" in msg                         # entry falls back
    assert "No reasoning recorded." in msg      # reason falls back


def test_paper_fill_bad_price_degrades():
    notifier, sent = _capture()
    ok = asyncio.run(
        notifier.send_paper_order_fill(symbol="MSFT", quantity=1, entry_price="not-a-number")
    )
    assert ok is True
    assert "n/a" in sent["message"]             # unparseable price -> n/a, no crash


# --- Markdown-entity 400 fix (watchdog night-watch bug, 2026-08-07) ----------

def test_escape_markdown_escapes_legacy_entity_chars():
    assert escape_markdown("position_cycle") == "position\\_cycle"
    assert escape_markdown("a*b`c[d") == "a\\*b\\`c\\[d"
    assert escape_markdown("plain text") == "plain text"


def test_paper_fill_reason_with_underscores_is_escaped():
    notifier, sent = _capture()
    ok = asyncio.run(
        notifier.send_paper_order_fill(
            symbol="AAPL", quantity=1, entry_price=100.0,
            reason="momentum_score beat threshold_v2",
        )
    )
    assert ok is True
    # Unescaped _ in LLM prose would 400 the whole send ("can't parse entities")
    assert "momentum\\_score beat threshold\\_v2" in sent["message"]


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return ""


class _FakeSession:
    """Captures the JSON payload sendMessage would post."""

    captured = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json=None, timeout=None):
        _FakeSession.captured = json
        return _FakeResponse()


def test_send_message_parse_mode_none_omits_field(monkeypatch):
    import app.modules.notifications.telegram as tg
    monkeypatch.setattr(tg.aiohttp, "ClientSession", _FakeSession)

    notifier = TelegramNotifier(bot_token="t", chat_id="c")

    # Watchdog/alert path: plain text, no parse_mode key at all
    ok = asyncio.run(notifier.send_message(
        "🚨 Pipeline watchdog — position cycle stale\nquant.position_cycle last ran never",
        parse_mode=None,
    ))
    assert ok is True
    assert "parse_mode" not in _FakeSession.captured
    assert "position_cycle" in _FakeSession.captured["text"]

    # Default path unchanged: Markdown still sent for formatted notifications
    ok = asyncio.run(notifier.send_message("*bold*"))
    assert ok is True
    assert _FakeSession.captured["parse_mode"] == "Markdown"
