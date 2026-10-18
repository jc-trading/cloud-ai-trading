"""Channel adapters: deliver one text through one ``NotificationChannel``.

Secrets are read from the environment variable the channel names
(``secret_env_key``), never from the database. A missing secret fails the
delivery before any client is built — ``TelegramNotifier(bot_token="")`` would
silently fall back to the default bot token.
"""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.config import settings
from app.modules.notifications import telegram

_sleep = asyncio.sleep
_ssl_context = ssl.create_default_context

SMTP_TIMEOUT_SECONDS = 10


@dataclass
class SendOutcome:
    ok: bool
    attempts: int
    error: str | None


def _env(key: str) -> str:
    return os.environ.get(key) or str(getattr(settings, key, "") or "")


async def send_telegram(channel, text: str) -> SendOutcome:
    key = channel.secret_env_key
    token = _env(key)
    if not token:
        return SendOutcome(False, 0, f"{key} not set")
    config = channel.config_json or {}
    chat = config.get("chat_id") or _env(config.get("chat_id_env") or "")
    if not chat:
        return SendOutcome(False, 0, "telegram chat id not set")
    notifier = telegram.TelegramNotifier(bot_token=token, chat_id=str(chat))
    # plain text: event messages carry unescaped names (signal_cycle, symbols)
    ok = await notifier.send_message(text, parse_mode=None)
    return SendOutcome(bool(ok), notifier.last_attempts,
                       None if ok else (notifier.last_error or "telegram send failed"))


def _smtp_send_blocking(host: str, port: int, user: str, password: str,
                        to: list[str], subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg.set_content(text)
    with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        smtp.starttls(context=_ssl_context())
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)


async def send_smtp(channel, subject: str, text: str) -> SendOutcome:
    config = channel.config_json or {}
    user_key = config.get("username_env") or "SMTP_USER"
    user = _env(user_key)
    if not user:
        return SendOutcome(False, 0, f"{user_key} not set")
    password = _env(channel.secret_env_key)
    if not password:
        return SendOutcome(False, 0, f"{channel.secret_env_key} not set")
    to = list(config.get("to") or []) or [user]
    host = config.get("host") or ""
    port = int(config.get("port") or 587)

    attempts_max = len(telegram.SEND_RETRY_BACKOFF) + 1
    error = None
    for attempt in range(1, attempts_max + 1):
        try:
            await asyncio.to_thread(_smtp_send_blocking, host, port, user, password,
                                    to, subject, text)
            return SendOutcome(True, attempt, None)
        except smtplib.SMTPAuthenticationError as exc:
            return SendOutcome(False, attempt, f"SMTPAuthenticationError: {exc}"[:2000])
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:2000]
        if attempt < attempts_max:
            await _sleep(telegram.SEND_RETRY_BACKOFF[attempt - 1])
    return SendOutcome(False, attempts_max, error)


async def send_via(channel, event_type: str, severity: str, text: str) -> SendOutcome:
    if channel.type == "telegram":
        return await send_telegram(channel, text)
    if channel.type == "smtp":
        return await send_smtp(channel, f"[CAT] {severity.upper()} · {event_type}", text)
    return SendOutcome(False, 0, f"unknown channel type {channel.type!r}")
