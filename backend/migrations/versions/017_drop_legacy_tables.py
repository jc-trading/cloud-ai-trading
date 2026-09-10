"""drop parked legacy tables (Phase B)

The equity/execution/analysis/risk/strategy/trading/exchange/fundamentals
modules are deleted: nothing reads or writes these tables any more. The
system-monitoring downgrade takes the last three with it — host metrics and
the request log lost their only producer/consumer, while liveness now lives in
``heartbeat_records`` + the backend watchdog.

Observation data (sim_*, recommendations, recommendation_outcomes, llm_calls,
market_data_files, market_stream_symbols, heartbeat_records, safety_state,
users, watchlists) is untouched.

Revision ID: 017
Revises: 016
"""
from typing import Sequence, Union
from alembic import op

revision: str = '017'
down_revision: Union[str, None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# children before parents (CASCADE covers it either way)
_TABLES = (
    "ai_analysis_results",
    "position_metrics",
    "drawdown_records",
    "risk_limits",
    "alerts",
    "alert_rules",
    "portfolio_stats",
    "positions",
    "trading_signals",
    "quant_strategies",
    "exchange_connections",
    "company_fundamentals",
    "earnings_calendar",
    "system_logs",
    "system_metrics",
    "task_status",
)


def upgrade() -> None:
    for t in _TABLES:
        op.execute(f'DROP TABLE IF EXISTS {t} CASCADE')


def downgrade() -> None:
    """Destructive migration — dropped legacy tables are not recreated."""
    # Same convention as 006/013: restore from a DB backup if ever needed. A
    # documented pass stub keeps a downgrade walk past 017 from breaking.
    pass
