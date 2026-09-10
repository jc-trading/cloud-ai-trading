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
from app.modules.watchlist.models import Watchlist, WatchlistItem  # noqa: F401
from app.modules.simledger.models import (  # noqa: F401
    SimAccount,
    SimPosition,
    SimOrder,
    SimFill,
    AccountSnapshot,
    SafetyState,
    HeartbeatRecord,
    MasterSetting,
    Recommendation,
)
from app.modules.llm.models import LLMCall  # noqa: F401
from app.modules.market.models import MarketDataFile, MarketStreamSymbol  # noqa: F401

__all__ = [
    "Base",
    "User",
    "Watchlist",
    "WatchlistItem",
    "SimAccount",
    "SimPosition",
    "SimOrder",
    "SimFill",
    "AccountSnapshot",
    "SafetyState",
    "HeartbeatRecord",
    "MasterSetting",
    "Recommendation",
    "LLMCall",
    "MarketDataFile",
    "MarketStreamSymbol",
]
