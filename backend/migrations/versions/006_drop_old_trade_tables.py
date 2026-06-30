"""Drop old trade tables that don't match current models

Revision ID: 006
Revises: 005
Create Date: 2026-04-13

These tables were created in migration 001 but the corresponding models
(Trade, SimulatePortfolio, ActivityLog) no longer exist. Current models
define Position, TradingSignal, Alert, AlertRule, PortfolioStats instead.
Dropping these old tables to avoid schema drift.
"""
from alembic import op


revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop old tables that no longer have models."""
    # Trade signals old version - conflicted with new TradingSignal model
    # The new model is in trading/models.py and was created via migration 004
    op.drop_table("trade_signals")

    # Old trading simulation portfolio tracking
    op.drop_table("simulate_portfolios")

    # Old activity log tracking
    op.drop_table("activity_logs")

    # Old trades table (had old model structure)
    op.drop_table("trades")


def downgrade() -> None:
    """Recreate dropped tables for rollback."""
    # Note: This is a destructive migration - downgrade would lose data
    # Keeping downgrade stub for completeness but it's not recommended to rollback
    pass
