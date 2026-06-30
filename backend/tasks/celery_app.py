"""
Celery application configuration.
Handles periodic tasks like market data pulling and AI analysis.
"""

from celery import Celery

from app.config import get_settings

# Import all ORM models before Celery workers execute tasks. Some relationships
# use string class names, so SQLAlchemy needs the full model registry loaded in
# worker processes, not only in the FastAPI process.
from app.modules.auth.models import User  # noqa: F401
from app.modules.exchange.models import ExchangeConnection  # noqa: F401
from app.modules.watchlist.models import Watchlist, WatchlistItem  # noqa: F401
from app.modules.market.models import MarketCandle  # noqa: F401
from app.modules.market_data.models import (  # noqa: F401
    OHLCVCandle,
    TechnicalIndicator,
    MarketDataEvent,
)
from app.modules.analysis.models import AIAnalysisResult  # noqa: F401
from app.modules.strategy.models import QuantStrategy  # noqa: F401
from app.modules.trading.models import (  # noqa: F401
    TradingSignal,
    AlertRule,
    Alert,
    Position,
    PortfolioStats,
)
from app.modules.risk.models import RiskLimit, PositionMetric, DrawdownRecord  # noqa: F401
from app.modules.system.models import SystemLog, SystemMetric, TaskStatus  # noqa: F401

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
)

# Auto-discover tasks in tasks/ directory and app tasks
celery_app.autodiscover_tasks(["tasks", "app.tasks"])

# Periodic task schedule (Celery Beat)
celery_app.conf.beat_schedule = {
    # Collect OHLCV market data every 1 minute
    "collect-market-data": {
        "task": "collect_market_data",
        "schedule": 60.0,  # every 60 seconds
    },
    # Update technical indicators every 2 minutes
    "update-indicators": {
        "task": "update_indicators",
        "schedule": 120.0,  # every 120 seconds
    },
    # Cleanup old market data daily
    "cleanup-market-data": {
        "task": "cleanup_market_data",
        "schedule": 86400.0,  # every 24 hours
    },
    # Pull market data every 1 minute (legacy task)
    "pull-market-data": {
        "task": "pull_market_data",
        "schedule": 60.0,  # every 60 seconds
    },
    # Run AI analysis every 3 minutes
    "run-ai-analysis": {
        "task": "run_scheduled_analysis",
        "schedule": settings.ANALYSIS_INTERVAL_MINUTES * 60,  # default: 180 seconds
    },
    # Sync watchlists every 5 minutes
    "sync-watchlists": {
        "task": "sync_watchlists",
        "schedule": 300.0,  # every 5 minutes
    },
    # Generate trading signals every 15 minutes
    "generate-trading-signals": {
        "task": "generate_trading_signals",
        "schedule": 900.0,  # every 900 seconds (15 minutes) — optimized for cost
    },
    # Calculate portfolio statistics every 1 hour
    "calculate-portfolio-stats": {
        "task": "calculate_portfolio_stats",
        "schedule": 3600.0,  # every 3600 seconds (1 hour)
    },
    # System Monitoring Tasks
    # Collect system metrics every 60 seconds
    "collect-system-metrics": {
        "task": "collect_system_metrics",
        "schedule": float(settings.SYSTEM_METRICS_COLLECTION_INTERVAL_SECONDS),
    },
    # Sync task statuses every configured interval
    "sync-task-statuses": {
        "task": "sync_task_statuses",
        "schedule": float(settings.SYSTEM_TASK_HEALTH_CHECK_INTERVAL_SECONDS),
    },
    # Cleanup old logs daily
    "cleanup-old-logs": {
        "task": "cleanup_old_logs",
        "schedule": 86400.0,  # every 24 hours
    },
    # Cleanup old metrics daily
    "cleanup-old-metrics": {
        "task": "cleanup_old_metrics",
        "schedule": 86400.0,  # every 24 hours
    },
}
