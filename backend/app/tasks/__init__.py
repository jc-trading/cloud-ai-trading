"""Celery task modules (v3): quant three-tier cycle + telegram commands, plus
the PARKED equity-catalyst companions (importable, nothing schedules them)."""

from app.tasks import quant_tasks, telegram_tasks  # noqa: F401
from app.tasks import equity_tasks, execution_tasks, fundamentals_tasks  # noqa: F401
