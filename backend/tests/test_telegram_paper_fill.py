"""Tests for TelegramNotifier.send_paper_order_fill.

Standalone — no network. ``send_message`` is patched to capture the built text,
so we assert the PAPER disclaimer + symbol / quantity / entry / reasoning are all
present and that missing fields degrade safely instead of raising.
"""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.notifications.telegram import TelegramNotifier


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
