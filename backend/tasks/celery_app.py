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
        "tasks.market_tasks",
        "tasks.analysis_tasks",
        "tasks.system_tasks",
        "app.tasks.market_data_tasks",
        "app.tasks.trading_tasks",
        "app.tasks.risk_tasks",
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
# packages) and masked missing includes; risk_tasks is now in `include` instead.

# Periodic task schedule (Celery Beat)
# Every entry sets options.expires (frequent tasks: just under their interval;
# crontab tasks: 1h, execution: 15 min) so that if the worker stalls and Beat
# keeps publishing, the recovered worker DISCARDS the stale backlog instead of
# replaying hours of queued pulls/analyses (the 2026-07-01 freeze left 10.5k).
celery_app.conf.beat_schedule = {
    # Collect OHLCV market data every 1 minute
    "collect-market-data": {
        "task": "collect_market_data",
        "schedule": 60.0,  # every 60 seconds
        "options": {"expires": 55},
    },
    # Update technical indicators every 2 minutes
    "update-indicators": {
        "task": "update_indicators",
        "schedule": 120.0,  # every 120 seconds
        "options": {"expires": 110},
    },
    # Cleanup old market data daily
    "cleanup-market-data": {
        "task": "cleanup_market_data",
        "schedule": 86400.0,  # every 24 hours
        "options": {"expires": 3600},
    },
    # Pull market data every 1 minute (legacy task)
    "pull-market-data": {
        "task": "pull_market_data",
        "schedule": 60.0,  # every 60 seconds
        "options": {"expires": 55},
    },
    # Run AI analysis every 3 minutes
    "run-ai-analysis": {
        "task": "run_scheduled_analysis",
        "schedule": settings.ANALYSIS_INTERVAL_MINUTES * 60,  # default: 180 seconds
        "options": {"expires": max(settings.ANALYSIS_INTERVAL_MINUTES * 60 - 10, 1)},
    },
    # Sync watchlists every 5 minutes
    "sync-watchlists": {
        "task": "sync_watchlists",
        "schedule": 300.0,  # every 5 minutes
        "options": {"expires": 290},
    },
    # Generate trading signals every 15 minutes
    "generate-trading-signals": {
        "task": "generate_trading_signals",
        "schedule": 900.0,  # every 900 seconds (15 minutes) — optimized for cost
        "options": {"expires": 890},
    },
    # Calculate portfolio statistics every 1 hour
    "calculate-portfolio-stats": {
        "task": "calculate_portfolio_stats",
        "schedule": 3600.0,  # every 3600 seconds (1 hour)
        "options": {"expires": 3590},
    },
    # System Monitoring Tasks
    # Collect system metrics every 60 seconds
    "collect-system-metrics": {
        "task": "collect_system_metrics",
        "schedule": float(settings.SYSTEM_METRICS_COLLECTION_INTERVAL_SECONDS),
        "options": {"expires": max(float(settings.SYSTEM_METRICS_COLLECTION_INTERVAL_SECONDS) - 5, 1)},
    },
    # Sync task statuses every configured interval
    "sync-task-statuses": {
        "task": "sync_task_statuses",
        "schedule": float(settings.SYSTEM_TASK_HEALTH_CHECK_INTERVAL_SECONDS),
        "options": {"expires": max(float(settings.SYSTEM_TASK_HEALTH_CHECK_INTERVAL_SECONDS) - 5, 1)},
    },
    # Cleanup old logs daily
    "cleanup-old-logs": {
        "task": "cleanup_old_logs",
        "schedule": 86400.0,  # every 24 hours
        "options": {"expires": 3600},
    },
    # Cleanup old metrics daily
    "cleanup-old-metrics": {
        "task": "cleanup_old_metrics",
        "schedule": 86400.0,  # every 24 hours
        "options": {"expires": 3600},
    },
    # Risk Management Tasks
    # Monitor portfolios (position metrics, drawdown, limit checks) every 5 minutes.
    # Deliberately NOT 1-minute: metrics only move with new candles and 1m would
    # spam position_metrics/drawdown_records rows.
    "risk-monitor-portfolio": {
        "task": "risk.monitor_portfolio",
        "schedule": 300.0,  # every 5 minutes
        "options": {"expires": 290},
    },
    # Check emergency conditions (daily loss limit, drawdown) every 5 minutes.
    "risk-check-emergency-conditions": {
        "task": "risk.check_emergency_conditions",
        "schedule": 300.0,  # every 5 minutes
        "options": {"expires": 290},
    },
    # Fundamentals cache refresh (Phase 3 FA). Scoped to watchlist equities only
    # and throttled inside each task to respect the Finnhub free-tier quota, so
    # the schedules are deliberately coarse.
    # Company profiles + historical financials — WEEKLY (Sunday 06:00 UTC).
    "refresh-company-profiles": {
        "task": "fundamentals.refresh_company_profiles",
        "schedule": crontab(minute=0, hour=6, day_of_week="sunday"),
        "options": {"expires": 3600},
    },
    # Earnings calendar + estimates — DAILY, pre-market (12:00 UTC ~ 08:00 ET,
    # before the 09:30 ET open).
    "refresh-earnings-calendar": {
        "task": "fundamentals.refresh_earnings_calendar",
        "schedule": crontab(minute=0, hour=12),
        "options": {"expires": 3600},
    },
    # Fill actual EPS/revenue for symbols that just reported — DAILY, after the
    # US close (23:30 UTC) so amc reports are already out.
    "refresh-financials-on-earnings": {
        "task": "fundamentals.refresh_financials_on_earnings",
        "schedule": crontab(minute=30, hour=23),
        "options": {"expires": 3600},
    },
    # Equity research schedule (EQUITY-schedule) — weekday, US-market-hours cron.
    # Times are UTC (the app runs UTC and a fixed crontab cannot follow DST); each
    # is chosen to hold year-round across EST/EDT:
    #   pre_market  12:00 UTC = 07:00 EST / 08:00 EDT  (before the 09:30 ET open)
    #   market_open 14:30 UTC = 09:30 EST / 10:30 EDT  (at/after the ET open)
    #   eod         21:30 UTC = 16:30 EST / 17:30 EDT  (after the 16:00 ET close)
    # day_of_week="mon-fri" gates weekends; each task ALSO skips US market holidays
    # (is_us_trading_day) so it only runs on real trading days.
    "equity-pre-market": {
        "task": "equity.pre_market",
        "schedule": crontab(minute=0, hour=12, day_of_week="mon-fri"),
        "options": {"expires": 3600},
    },
    "equity-market-open": {
        "task": "equity.market_open",
        "schedule": crontab(minute=30, hour=14, day_of_week="mon-fri"),
        "options": {"expires": 3600},
    },
    "equity-eod": {
        "task": "equity.eod",
        "schedule": crontab(minute=30, hour=21, day_of_week="mon-fri"),
        "options": {"expires": 3600},
    },
    # Auto-execution (EXEC-auto-task) — weekday, just AFTER the equity market-open
    # run. market_open (14:30 UTC) stamps "order intent" on today's GO Decisions;
    # this fires 5 min later to place the actual PAPER Alpaca BUYs inside the risk
    # budget. 14:35 UTC = 09:35 EST / 10:35 EDT (after the 09:30 ET open, year-round).
    # day_of_week="mon-fri" gates weekends; the task ALSO skips US market holidays
    # (is_us_trading_day). PAPER + equity ONLY — crypto/Binance is never executed.
    "execution-auto-execute-equity": {
        "task": "execution.auto_execute_equity",
        "schedule": crontab(minute=35, hour=14, day_of_week="mon-fri"),
        "options": {"expires": 900},
    },
}

# --- R0-0 quiesce (2026-07-27) --------------------------------------------
# The legacy crypto signal pipeline (retired) and the old equity catalyst
# auto-execution (parked) are being replaced by the deterministic-quant rebuild
# (see CAT_merged_plan_v2.0 / CAT_execution_plan_R0-R1). Disable their Beat
# entries NOW so that during the rebuild nothing spends Claude quota (the 3-min
# run_scheduled_analysis was the main cost) and nothing places orders. The task
# *code* is untouched here — crypto is deleted in R1-8, and the whole schedule
# is rewritten into the three-tier cadence in R1-2. Fully reversible: remove
# this block to restore the original cadence.
_R0_0_QUIESCED = [
    # crypto signal pipeline
    "collect-market-data",
    "update-indicators",
    "pull-market-data",
    "run-ai-analysis",           # 3-min Claude fusion — the main $ cost
    "generate-trading-signals",  # 15-min crypto signals
    # old equity catalyst (parked) + its auto-execution
    "equity-pre-market",
    "equity-market-open",
    "equity-eod",
    "execution-auto-execute-equity",
    # R1-0 (2026-07-30): hourly portfolio Telegram belongs to the retired legacy
    # loop — silenced until the R1-2 three-tier schedule replaces it
    "calculate-portfolio-stats",
]
for _quiesced_key in _R0_0_QUIESCED:
    celery_app.conf.beat_schedule.pop(_quiesced_key, None)
