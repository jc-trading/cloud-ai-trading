"""Single source of truth for the full ORM model registry.

Importing this module loads *every* model module exactly once, so the complete
set of tables is attached to ``Base.metadata``. Every process that needs the
full registry must import this module instead of maintaining its own hand-kept
import list:

  - FastAPI app (``app/main.py``)        — string-based relationships resolve
  - Celery workers (``tasks/celery_app.py``)
  - Alembic autogenerate (``migrations/env.py``) — so no table silently drifts

When a new model module is added, register it HERE once and all three pick it
up automatically. This is what prevents the env.py-vs-app drift that previously
hid the risk tables from migrations.
"""

from app.database import Base  # noqa: F401

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
from app.modules.fundamentals.models import CompanyFundamentals, EarningsCalendar  # noqa: F401
from app.modules.system.models import SystemLog, SystemMetric, TaskStatus  # noqa: F401

__all__ = [
    "Base",
    "User",
    "ExchangeConnection",
    "Watchlist",
    "WatchlistItem",
    "MarketCandle",
    "OHLCVCandle",
    "TechnicalIndicator",
    "MarketDataEvent",
    "AIAnalysisResult",
    "QuantStrategy",
    "TradingSignal",
    "AlertRule",
    "Alert",
    "Position",
    "PortfolioStats",
    "RiskLimit",
    "PositionMetric",
    "DrawdownRecord",
    "CompanyFundamentals",
    "EarningsCalendar",
    "SystemLog",
    "SystemMetric",
    "TaskStatus",
]
