#!/usr/bin/env python3
"""Simple script to test Telegram notification."""

import asyncio
import sys
from pathlib import Path

__test__ = False

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.modules.notifications.telegram import TelegramNotifier
from app.config import settings


async def test_simple_message():
    """Test sending a simple message to Telegram."""
    print("🧪 Testing Telegram simple message...")
    print(f"Bot Token configured: {bool(settings.TELEGRAM_BOT_TOKEN)}")
    print(f"Chat ID: {settings.TELEGRAM_CHAT_ID}")

    notifier = TelegramNotifier()

    message = "✅ Telegram test message from CloudAiTrading - System is working!"
    result = await notifier.send_message(message)

    if result:
        print("✅ Message sent successfully!")
    else:
        print("❌ Failed to send message")

    return result


async def test_trading_signal():
    """Test sending a trading signal."""
    print("\n🧪 Testing Telegram trading signal...")

    notifier = TelegramNotifier()
    result = await notifier.send_trading_signal(
        symbol="BTC/USDT",
        signal_type="STRONG_BUY",
        signal_strength=85.5,
        confidence=92.0,
        recommendation="Strong uptrend detected. Entry recommended at current levels."
    )

    if result:
        print("✅ Trading signal sent successfully!")
    else:
        print("❌ Failed to send trading signal")

    return result


async def test_position_alert():
    """Test sending a position alert."""
    print("\n🧪 Testing Telegram position alert...")

    notifier = TelegramNotifier()
    result = await notifier.send_position_alert(
        symbol="ETH/USDT",
        action="opened",
        entry_price=2450.50,
        quantity=1.0
    )

    if result:
        print("✅ Position alert sent successfully!")
    else:
        print("❌ Failed to send position alert")

    return result


async def main():
    """Run all tests."""
    print("=" * 50)
    print("TELEGRAM NOTIFICATION TESTS")
    print("=" * 50)

    try:
        # Test simple message
        simple_ok = await test_simple_message()

        # Test trading signal
        signal_ok = await test_trading_signal()

        # Test position alert
        position_ok = await test_position_alert()

        print("\n" + "=" * 50)
        print("TEST SUMMARY")
        print("=" * 50)
        print(f"Simple Message: {'✅ PASSED' if simple_ok else '❌ FAILED'}")
        print(f"Trading Signal: {'✅ PASSED' if signal_ok else '❌ FAILED'}")
        print(f"Position Alert: {'✅ PASSED' if position_ok else '❌ FAILED'}")
        print("=" * 50)

        if all([simple_ok, signal_ok, position_ok]):
            print("\n🎉 All Telegram tests passed!")
            return 0
        else:
            print("\n⚠️  Some tests failed. Check your Telegram config.")
            return 1

    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
