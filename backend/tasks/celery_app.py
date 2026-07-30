"""
Celery application configuration.
Handles periodic tasks like market data pulling and AI analysis.
"""

from celery import Celery
from celery.schedules import crontab

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
        # host health
        "tasks.system_tasks",
        # PARKED (importable, nothing schedules them): equity catalyst pipeline
        # + its execution/fundamentals/risk companions await a future decision
        "app.tasks.fundamentals_tasks",
        "app.tasks.equity_tasks",
        "app.tasks.execution_tasks",
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
    # child is replaced with a fresh process. Generous on purpose — the coarse
    # fundamentals/equity tasks legitimately run for minutes; the goal is only
    # that a hung child never wedges the worker permanently (2026-07-01 freeze).
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
celery_app.conf.beat_schedule = {
    # --- system health -----------------------------------------------------
    "collect-system-metrics": {
        "task": "collect_system_metrics",
        "schedule": float(settings.SYSTEM_METRICS_COLLECTION_INTERVAL_SECONDS),
        "options": {"expires": max(float(settings.SYSTEM_METRICS_COLLECTION_INTERVAL_SECONDS) - 5, 1)},
    },
    "sync-task-statuses": {
        "task": "sync_task_statuses",
        "schedule": float(settings.SYSTEM_TASK_HEALTH_CHECK_INTERVAL_SECONDS),
        "options": {"expires": max(float(settings.SYSTEM_TASK_HEALTH_CHECK_INTERVAL_SECONDS) - 5, 1)},
    },
    "cleanup-old-logs": {
        "task": "cleanup_old_logs",
        "schedule": 86400.0,
        "options": {"expires": 3600},
    },
    "cleanup-old-metrics": {
        "task": "cleanup_old_metrics",
        "schedule": 86400.0,
        "options": {"expires": 3600},
    },
}

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
    # The two entry slots below must fire inside cycles.ENTRY_WINDOW_ET
    # (app/modules/simledger/cycles.py) — THE authoritative entry window; the
    # task re-gates itself on it, so a slot outside the window silently no-ops.
    "quant-entry-cycle-edt": {
        "task": "quant.entry_cycle",
        "schedule": crontab(hour=13, minute=36),   # 09:36 ET during EDT
        "options": {"expires": 900},
    },
    "quant-entry-cycle-est": {
        "task": "quant.entry_cycle",
        "schedule": crontab(hour=14, minute=36),   # 09:36 ET during EST
        "options": {"expires": 900},
    },
    "quant-signal-cycle": {
        "task": "quant.signal_cycle",
        "schedule": crontab(hour=21, minute=30),   # post-close in both regimes
        "options": {"expires": 3300},
    },
})
