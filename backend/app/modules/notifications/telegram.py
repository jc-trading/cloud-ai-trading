"""Telegram notification service."""

import logging
import aiohttp
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


def escape_markdown(text: str) -> str:
    """Escape a dynamic fragment for Telegram legacy Markdown parse mode.

    Legacy Markdown treats ``_ * ` [`` as entity openers; an unpaired one in
    dynamic text (task names like ``position_cycle``, LLM prose) makes the
    whole sendMessage 400 with "can't parse entities". Escape all four.
    """
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


class TelegramNotifier:
    """Send notifications via Telegram."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(self, message: str, parse_mode: Optional[str] = "Markdown") -> bool:
        """
        Send a message to Telegram.

        Args:
            message: Message text (supports Markdown by default)
            parse_mode: Telegram parse mode; pass ``None`` to send plain text —
                required for messages built from dynamic strings (task names,
                symbols, exception text) that are not Markdown-escaped.

        Returns:
            True if successful, False otherwise
        """
        if not self.bot_token or not self.chat_id:
            logger.warning(
                "Telegram credentials not configured. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )
            return False

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                }
                if parse_mode is not None:
                    payload["parse_mode"] = parse_mode
                async with session.post(
                    f"{self.api_url}/sendMessage",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        logger.info(f"Telegram message sent: {message[:50]}...")
                        return True
                    else:
                        logger.error(
                            f"Telegram API error: {response.status} - {await response.text()}"
                        )
                        return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_trading_signal(
        self,
        symbol: str,
        signal_type: str,
        signal_strength: float,
        confidence: float,
        recommendation: str,
    ) -> bool:
        """Send trading signal notification."""
        emoji = {
            "STRONG_BUY": "🚀",
            "BUY": "📈",
            "HOLD": "⏸️",
            "SELL": "📉",
            "STRONG_SELL": "💥",
        }

        emoji_char = emoji.get(signal_type, "📊")

        message = (
            f"{emoji_char} *Trading Signal*\n\n"
            f"Symbol: `{symbol}`\n"
            f"Signal: *{signal_type}*\n"
            f"Strength: {signal_strength:.1f}%\n"
            f"Confidence: {confidence:.1f}%\n\n"
            f"__{recommendation}__"
        )

        return await self.send_message(message)

    async def send_position_alert(
        self,
        symbol: str,
        action: str,  # "opened" or "closed"
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        quantity: float = None,
        pnl: Optional[float] = None,
    ) -> bool:
        """Send position alert notification."""
        if action == "opened":
            message = (
                f"📍 *Position Opened*\n\n"
                f"Symbol: `{symbol}`\n"
                f"Entry Price: ${entry_price:.2f}\n"
                f"Quantity: {quantity}\n"
                f"Total Invested: ${entry_price * quantity:.2f}"
            )
        else:  # closed
            pnl_emoji = "✅" if pnl and pnl > 0 else "❌"
            pnl_color = "+" if pnl and pnl > 0 else ""

            message = (
                f"{pnl_emoji} *Position Closed*\n\n"
                f"Symbol: `{symbol}`\n"
                f"Entry: ${entry_price:.2f}\n"
                f"Exit: ${exit_price:.2f}\n"
                f"Quantity: {quantity}\n"
                f"P&L: *{pnl_color}${pnl:.2f}*"
            )

        return await self.send_message(message)

    async def send_paper_order_fill(
        self,
        symbol: str,
        quantity=None,
        entry_price=None,
        reason: Optional[str] = None,
    ) -> bool:
        """Notify a filled **PAPER** equity BUY.

        Carries the standing PAPER disclaimer plus the symbol / quantity / entry
        price and a short reasoning summary from the linked Decision. Fully
        None-safe — missing fields degrade to a placeholder, never raise.
        """
        sym = symbol or "?"

        qty_str = "?" if quantity is None else str(quantity)

        try:
            price_str = f"${float(entry_price):.2f}" if entry_price is not None else "n/a"
        except (TypeError, ValueError):
            price_str = "n/a"

        reason_str = (reason or "").strip() or "No reasoning recorded."
        if len(reason_str) > 400:
            reason_str = reason_str[:397] + "..."
        # LLM prose may contain _ * ` [ — unescaped they 400 the whole send
        reason_str = escape_markdown(reason_str)

        message = (
            f"🧾 *PAPER Order Filled*\n\n"
            f"⚠️ This is a *PAPER* trade — simulated, no real money.\n\n"
            f"Symbol: `{sym}`\n"
            f"Quantity: {qty_str}\n"
            f"Entry: {price_str}\n\n"
            f"_Why:_ {reason_str}"
        )

        return await self.send_message(message)

    async def send_portfolio_update(
        self,
        total_invested: float,
        current_value: float,
        total_pnl: float,
        return_percent: float,
        win_rate: float,
    ) -> bool:
        """Send portfolio statistics update."""
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        pnl_sign = "+" if total_pnl >= 0 else ""

        message = (
            f"{pnl_emoji} *Portfolio Update*\n\n"
            f"Total Invested: ${total_invested:.2f}\n"
            f"Current Value: ${current_value:.2f}\n"
            f"P&L: *{pnl_sign}${total_pnl:.2f}*\n"
            f"Return: *{pnl_sign}{return_percent:.2f}%*\n"
            f"Win Rate: {win_rate:.1f}%"
        )

        return await self.send_message(message)
