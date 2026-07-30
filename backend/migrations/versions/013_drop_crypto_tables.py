"""drop retired crypto data-plane tables (R1-8)

The crypto pipeline is deleted for good (Direction v2/v3: stocks+ETF only).
ohlcv_candles / technical_indicators / market_data_events were the Binance
collect plane; market_candles was an always-empty cache. Bars now live in
Parquet behind quant.data.get_bars().

Revision ID: 013
Revises: 012
"""
from typing import Sequence, Union
from alembic import op

revision: str = '013'
down_revision: Union[str, None] = '012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("technical_indicators", "ohlcv_candles", "market_data_events",
           "market_candles")


def upgrade() -> None:
    for t in _TABLES:
        op.execute(f'DROP TABLE IF EXISTS {t} CASCADE')


def downgrade() -> None:
    # historical crypto tables are not recreated — restore from a DB backup if
    # ever needed (they were empty/dead at drop time)
    raise NotImplementedError("crypto tables are gone for good (R1-8)")
