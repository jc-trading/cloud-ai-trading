"""
Celery application configuration.
Handles periodic tasks like market data pulling and AI analysis.
"""

import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_ready

from app.config import get_settings

# Import all ORM models before Celery workers execute tasks. Some relationships
# use string class names, so SQLAlchemy needs the full model registry loaded in
# worker processes, not only in the FastAPI process. The registry is shared with
# the FastAPI app and Alembic so the import lists cannot drift apart.
import app.models_registry  # noqa: F401

settings = get_settings()

celery_app = Celery(
    "cloud_ai_trading",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        # v3 platform (R1): the three-tier quant schedule + telegram commands
        "app.tasks.quant_tasks",
        "app.tasks.telegram_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Self-healing backstop: a task that blocks forever (e.g. a network call
    # whose timeout never fires) is SIGKILLed at the hard limit and the pool
    # child is replaced with a fresh process. Generous on purpose — signal_cycle
    # legitimately runs for minutes; the goal is only that a hung child never
    # wedges the worker permanently (2026-07-01 freeze).
    task_soft_time_limit=1500,  # 25 min: raises SoftTimeLimitExceeded in-task
    task_time_limit=1800,  # 30 min: hard kill + replace the pool child
)

# NOTE: no autodiscover_tasks() — every task module is registered explicitly via
# the `include=[...]` list above. autodiscover was redundant (it re-scanned the same
# packages) and masked missing includes — every task module must be listed explicitly above.

# Periodic task schedule (Celery Beat)
# Every entry sets options.expires (frequent tasks: just under their interval;
# crontab tasks: 1h, execution: 15 min) so that if the worker stalls and Beat
# keeps publishing, the recovered worker DISCARDS the stale backlog instead of
# replaying hours of queued pulls/analyses (the 2026-07-01 freeze left 10.5k).
celery_app.conf.beat_schedule = {}

# R0-0 quiesce block removed in R1-8 (2026-07-30): the crypto pipeline and the
# old catalyst schedules it silenced are deleted/unscheduled for good.
# --- R1-2 three-tier quant schedule (2026-07-30, Direction v3) --------------
# signal-level daily post-close · position-level 5-min in RTH · heartbeat 1-min.
# All tasks re-gate themselves on the XNYS calendar internally; the two entry
# slots cover EDT/EST opens (the wrong one no-ops). All carry expires so a
# recovering worker discards stale backlog (07-04 incident rules).
celery_app.conf.beat_schedule.update({
    "quant-heartbeat": {
        "task": "quant.heartbeat",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    "quant-telegram-poll": {
        "task": "quant.telegram_poll",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    "quant-position-cycle": {
        "task": "quant.position_cycle",
        "schedule": 300.0,          # gated to RTH inside the task
        "options": {"expires": 290},
    },
    # v3.1: intraday entry timing — every 15 min through RTH (self-gated on the
    # XNYS calendar + RTH inside the task). A shortlisted name enters the first
    # cycle its price is a good entry (run_entries' chase cap); the idempotency
    # key caps it at one entry per symbol per day.
    "quant-entry-cycle": {
        "task": "quant.entry_cycle",
        "schedule": 900.0,          # 15 min; RTH-gated inside the task
        "options": {"expires": 870},
    },
    # 方案 Phase 8: EOD SIP correction — 01:30 UTC is past 20:00 ET + the
    # free-tier 15-min SIP delay in both EDT and EST; the task re-gates itself
    # on the XNYS calendar and only touches a session that has closed.
    "market-eod-correction": {
        "task": "market.eod_correction",
        "schedule": crontab(hour=1, minute=30),
        "options": {"expires": 3300},
    },
    "quant-signal-cycle": {
        "task": "quant.signal_cycle",
        "schedule": crontab(hour=21, minute=30),   # post-close in both regimes
        "options": {"expires": 3300},
    },
})


async def check_master_settings() -> list[str]:
    """Worker-side §8.6 check. Alert-only on purpose: a worker that refuses to
    boot stops trading altogether, while a rejected settings row already falls
    back to its constant — loud beats dead."""
    from app.celery_database import CeleryAsyncSessionLocal
    from app.modules.simledger.settings import validate_settings

    async with CeleryAsyncSessionLocal() as db:
        problems = await validate_settings(db)
    if problems:
        detail = "; ".join(problems)
        logging.getLogger(__name__).error(
            "master_settings validation failed: %s", detail)
        from app.tasks.quant_tasks import _notify
        await _notify("⚠️ master_settings 有非法行（只允许收紧）— worker 用常量在跑: "
                      + detail)
    return problems


@worker_ready.connect
def _on_worker_ready(**_kw):
    from app.tasks.quant_tasks import _run_async

    try:
        _run_async(check_master_settings())
    except Exception:
        logging.getLogger(__name__).exception("master_settings validation failed to run")
