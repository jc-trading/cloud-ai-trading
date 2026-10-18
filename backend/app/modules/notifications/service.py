"""Notification dispatch: route one business event to its configured channels.

``notify`` runs inline in the caller (never through Celery — an alert must not
depend on the broker or a worker) and never raises. If the routing rules cannot
be read (tables missing before the migration or after a rollback, DB down or
not answering within ROUTES_TIMEOUT_SECONDS) or the event is unknown, it falls
back to the pre-P4 behaviour: one plain-text Telegram message through the
env-configured bot. A critical event that no configured channel delivered gets
the same fallback, so disabling every route can never silence it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import celery_database
from app.modules.notifications import telegram
from app.modules.notifications.channels import SendOutcome, _env, send_via
from app.modules.notifications.models import (
    EVENT_TYPES, SEVERITIES, NotificationChannel, NotificationLog, NotificationRule,
)

logger = logging.getLogger(__name__)

ROUTES_TIMEOUT_SECONDS = 5
LEGACY_FALLBACK_CHANNEL = "legacy-fallback (env telegram)"


def severity_passes(severity: str, min_severity: str) -> bool:
    return SEVERITIES.index(severity) >= SEVERITIES.index(min_severity)


def in_quiet_hours(quiet_hours, now_utc: datetime) -> bool:
    if not quiet_hours:
        return False
    try:
        start = time.fromisoformat(quiet_hours["start"])
        end = time.fromisoformat(quiet_hours["end"])
        t = now_utc.astimezone(ZoneInfo(quiet_hours["tz"])).time().replace(tzinfo=None)
    except Exception:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


async def _legacy_telegram(text: str) -> bool:
    try:
        return bool(await telegram.TelegramNotifier().send_message(text, parse_mode=None))
    except Exception:
        logger.warning("legacy telegram notify failed", exc_info=True)
        return False


def _log_row(channel, event_type: str, severity: str, status: str, text: str,
             outcome: SendOutcome | None = None) -> NotificationLog:
    return NotificationLog(
        channel_id=channel.id, channel_name=channel.name, event_type=event_type,
        severity=severity, status=status,
        error=outcome.error[:2000] if outcome and outcome.error else None,
        retry_count=max(outcome.attempts - 1, 0) if outcome else 0,
        message_preview=text[:200],
    )


def _is_legacy_destination(channel) -> bool:
    if channel.type != "telegram" or channel.secret_env_key != "TELEGRAM_BOT_TOKEN":
        return False
    config = channel.config_json or {}
    chat = config.get("chat_id") or _env(config.get("chat_id_env") or "")
    return str(chat) == _env("TELEGRAM_CHAT_ID")


async def _load_routes(event_type: str) -> list:
    async with celery_database.CeleryAsyncSessionLocal() as db:
        return (await db.execute(
            select(NotificationRule, NotificationChannel)
            .join(NotificationChannel, NotificationChannel.id == NotificationRule.channel_id)
            .where(NotificationRule.event_type == event_type,
                   NotificationRule.enabled.is_(True),
                   NotificationChannel.enabled.is_(True))
        )).all()


async def _write_logs(logs: list[NotificationLog]) -> None:
    async with celery_database.CeleryAsyncSessionLocal() as db:
        db.add_all(logs)
        await db.commit()


async def notify(event_type: str, severity: str, payload: dict) -> bool:
    """Deliver ``payload["text"]`` to every enabled channel whose enabled rule
    for ``event_type`` admits ``severity``. Non-critical events inside a rule's
    quiet hours are logged as ``suppressed`` and not sent later. Each delivery
    is logged in a separate session; a log-write failure is swallowed. Both DB
    round trips are bounded so a half-open database cannot stall the caller.
    Returns True when at least one channel accepted the message."""
    text = str((payload or {}).get("text") or "")
    if not text:
        logger.error("notify(%s, %s) called without text", event_type, severity)
        return False
    if event_type not in EVENT_TYPES or severity not in SEVERITIES:
        return await _legacy_telegram(text)

    try:
        pairs = await asyncio.wait_for(_load_routes(event_type), ROUTES_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("notify(%s): routing rules not read within %ss — legacy telegram",
                       event_type, ROUTES_TIMEOUT_SECONDS)
        return await _legacy_telegram(text)
    except Exception:
        logger.warning("notify(%s): routing rules unreadable — legacy telegram",
                       event_type, exc_info=True)
        return await _legacy_telegram(text)

    now = datetime.now(timezone.utc)
    logs: list[NotificationLog] = []
    deliveries: dict = {}
    for rule, channel in pairs:
        if not severity_passes(severity, rule.min_severity):
            continue
        if severity != "critical" and in_quiet_hours(rule.quiet_hours, now):
            logs.append(_log_row(channel, event_type, severity, "suppressed", text))
            continue
        deliveries.setdefault(channel.id, channel)

    channels = list(deliveries.values())
    results = await asyncio.gather(
        *(send_via(c, event_type, severity, text) for c in channels),
        return_exceptions=True)
    outcomes = [r if isinstance(r, SendOutcome) else SendOutcome(False, 1, repr(r)[:500])
                for r in results]
    for channel, outcome in zip(channels, outcomes):
        logs.append(_log_row(channel, event_type, severity,
                             "sent" if outcome.ok else "failed", text, outcome))

    delivered = any(o.ok for o in outcomes)
    if severity == "critical" and not delivered and any(
            _is_legacy_destination(c) for c in channels):
        logger.warning("notify(%s): critical not delivered; legacy fallback skipped — "
                       "a failed channel is the env-default Telegram destination",
                       event_type)
    elif severity == "critical" and not delivered:
        delivered = await _legacy_telegram(text)
        logs.append(NotificationLog(
            channel_id=None, channel_name=LEGACY_FALLBACK_CHANNEL, event_type=event_type,
            severity=severity, status="sent" if delivered else "failed",
            error=None if delivered else "legacy telegram fallback failed",
            retry_count=0, message_preview=text[:200]))

    if logs:
        try:
            await asyncio.wait_for(_write_logs(logs), ROUTES_TIMEOUT_SECONDS)
        except Exception:
            logger.warning("notify(%s): delivery log write failed", event_type, exc_info=True)
    return delivered


async def send_test(db, channel) -> SendOutcome:
    """Send a fixed test message through ``channel`` regardless of rules or
    quiet hours; the log row is added to ``db`` for the caller to commit."""
    text = (f"✅ CAT notification test — {channel.name} ({channel.type}) at "
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    try:
        outcome = await send_via(channel, "test", "info", text)
    except Exception as exc:
        outcome = SendOutcome(False, 1, repr(exc)[:500])
    db.add(_log_row(channel, "test", "info", "sent" if outcome.ok else "failed",
                    text, outcome))
    return outcome
