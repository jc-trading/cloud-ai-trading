"""Pipeline watchdog — detects a dead/wedged Celery worker and alerts.

Born from the 2026-07-01 incident: both worker children froze mid-task, the
main process still answered `celery inspect ping`, Beat kept publishing, and
for 3 days every surface looked green (containers Up, /api/health 200) while
10.5k tasks piled up and zero decisions were written. `ping` proves the main
process is alive, not that work is being consumed — so this watchdog runs in
the BACKEND process (which survives a wedged worker) and checks the two
signals that cannot lie:

  1. Broker queue depth — a stalled worker piles up Beat's publishes past
     QUEUE_DEPTH_ALERT within ~1.5h.
  2. Quant heartbeats — worker liveness, RTH stop monitoring, and the nightly
     signal cycle measured against its last EXPECTED session run.
  3. Sim-ledger invariants — every open lot must carry a usable stop.

Alerts go to Telegram (already wired for signals/orders) with a per-check
cooldown, and are always logged at ERROR for the container logs.
"""

import asyncio
import logging
import time

from sqlalchemy import func, select

from app.config import settings
from app.modules.notifications import TelegramNotifier

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 300  # every 5 minutes
QUEUE_DEPTH_ALERT = 200  # ~1.5h of Beat publishing with nothing consuming
ALERT_COOLDOWN_SECONDS = 6 * 3600  # per-check re-alert throttle

_last_alert_at: dict[str, float] = {}


async def _alert(check: str, message: str) -> None:
    """Log the problem and Telegram it, at most once per cooldown per check."""
    logger.error("WATCHDOG %s: %s", check, message)
    now = time.monotonic()
    last = _last_alert_at.get(check)
    if last is not None and now - last < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert_at[check] = now
    try:
        await TelegramNotifier().send_message(f"🚨 *Pipeline watchdog — {check}*\n{message}")
    except Exception as e:  # alerting must never take the watchdog down
        logger.error("Watchdog failed to send Telegram alert: %s", e)


async def _check_queue_depth() -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.CELERY_BROKER_URL,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        depth = await client.llen("celery")
    finally:
        await client.aclose()

    if depth > QUEUE_DEPTH_ALERT:
        await _alert(
            "queue backlog",
            f"{depth} tasks queued (alert threshold {QUEUE_DEPTH_ALERT}). "
            f"The Celery worker is likely wedged or down — check "
            f"`docker logs cat_celery_worker` and restart it.",
        )
    else:
        logger.debug("Watchdog queue depth OK: %s", depth)


# _check_decision_freshness was deleted in the R1 review fix round (#21): it
# monitored the retired crypto analysis pipeline — its producers no longer
# exist, so it could only false-alert (legacy rows) or stay silently dead.
# v3 liveness lives in _check_quant_heartbeats below.


# --- R1-5 quant-era checks (2026-07-30) -------------------------------------
# The v3 sim platform's liveness truth lives in the heartbeats table (written by
# quant.* tasks) and the sim ledger. These are the two-layer watchdog's INNER
# layer; the outer layer is an external ping service (optional until VPS).

WORKER_HEARTBEAT_STALE = 5 * 60          # quant.heartbeat runs every 60s
POSITION_CYCLE_STALE_RTH = 20 * 60       # runs every 5 min during RTH
SIGNAL_CYCLE_GRACE = 2 * 3600            # slack after the scheduled 21:30 UTC run


async def _check_quant_heartbeats() -> None:
    from app.database import AsyncSessionLocal
    from app.modules.simledger.models import HeartbeatRecord

    async with AsyncSessionLocal() as db:
        rows = {r.name: r for r in (
            await db.execute(select(HeartbeatRecord))).scalars().all()}

    now = time.time()
    worker = rows.get("worker")
    if worker is not None and now - worker.last_beat_at.timestamp() > WORKER_HEARTBEAT_STALE:
        await _alert(
            "worker heartbeat stale",
            f"quant.heartbeat last wrote {(now - worker.last_beat_at.timestamp()) / 60:.0f}m "
            f"ago (limit {WORKER_HEARTBEAT_STALE // 60}m) — the worker is wedged or "
            f"down. This is the 07-01 failure mode; restart cat_celery_worker.",
        )

    try:
        from quant.data import calendar as qcal
        from app.modules.simledger.cycles import in_rth, now_et as _now_et
        now_et = _now_et()
        trading_day = qcal.is_trading_day(now_et.date())
    except Exception:
        return  # calendar unavailable — heartbeat ages alone still covered above

    # 10-min open grace: the first position_cycle beat lands ~09:35-09:40 ET
    if in_rth(now_et, open_grace_min=10):
        pc = rows.get("position_cycle")
        if pc is None or now - pc.last_beat_at.timestamp() > POSITION_CYCLE_STALE_RTH:
            age = "never" if pc is None else f"{(now - pc.last_beat_at.timestamp()) / 60:.0f}m ago"
            await _alert(
                "position cycle stale",
                f"Market is OPEN but quant.position_cycle last ran {age} — open sim "
                f"positions have NO stop monitoring right now.",
            )

    # review #28: measure against the last EXPECTED run (previous session's
    # 21:30 UTC + grace), not a fixed wall-clock age — a fixed 26h false-alerts
    # every Monday and post-holiday morning.
    sc = rows.get("signal_cycle")
    if sc is not None:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        probe = now_et.date()
        expected = None
        for _ in range(10):     # walk back to the newest session whose run is due
            if qcal.is_trading_day(probe):
                run_at = _dt(probe.year, probe.month, probe.day, 21, 30,
                             tzinfo=_tz.utc).timestamp() + SIGNAL_CYCLE_GRACE
                if run_at <= now:
                    expected = run_at
                    break
            probe = probe - _td(days=1)
        if expected is not None and sc.last_beat_at.timestamp() < expected - SIGNAL_CYCLE_GRACE:
            await _alert(
                "signal cycle missed",
                f"quant.signal_cycle last completed "
                f"{(now - sc.last_beat_at.timestamp()) / 3600:.0f}h ago and the "
                f"latest scheduled run is overdue — no fresh recommendations/"
                f"exit management. Check beat + worker logs.",
            )


async def _check_sim_stops() -> None:
    """TOP-severity: every open sim lot must carry a usable stop — a lot without
    one has NO exit protection (the internal analog of 'every position has a
    live stop order at the broker')."""
    from app.database import AsyncSessionLocal
    from app.modules.simledger.models import SimPosition

    async with AsyncSessionLocal() as db:
        bad = list((await db.execute(
            select(SimPosition.symbol).where(
                SimPosition.status == "open",
                (SimPosition.stop.is_(None)) | (SimPosition.stop <= 0))
        )).scalars().all())
    if bad:
        await _alert(
            "OPEN POSITION WITHOUT STOP",
            f"Open sim lots with no usable stop: {', '.join(bad)} — exit "
            f"protection is missing; investigate immediately.",
        )


async def run_watchdog() -> None:
    """Background loop; started from the FastAPI lifespan, cancelled on shutdown."""
    logger.info(
        "Pipeline watchdog started (every %ss: queue depth > %s, quant "
        "heartbeats, sim stops)",
        CHECK_INTERVAL_SECONDS,
        QUEUE_DEPTH_ALERT,
    )
    checks = (
        ("queue", _check_queue_depth),
        ("quant heartbeats", _check_quant_heartbeats),
        ("sim stops", _check_sim_stops),
    )
    while True:
        for name, check in checks:
            try:
                await check()
            except Exception as e:
                logger.warning("Watchdog %s check failed: %s", name, e)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
