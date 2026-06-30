"""Add market_type to watchlist_items

Revision ID: 002_watchlist_market_type
Revises: 001_initial
Create Date: 2026-04-12
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002_watchlist_market_type"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add market_type column — "crypto" for BTC/USDT-style, "stock" for AAPL-style
    op.add_column(
        "watchlist_items",
        sa.Column("market_type", sa.String(10), server_default="crypto", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("watchlist_items", "market_type")
