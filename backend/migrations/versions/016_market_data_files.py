"""market data file registry + realtime subscription set (2026-09-08)

Adds ``market_data_files`` — one row per Parquet bar file under ``BARS_ROOT``,
carrying the provenance the new store enforces: a file has exactly ONE provider
(``alpaca:sip`` / ``alpaca:iex``), and an incremental write must be same-source
or replace the file outright. This table also retires the SQLite bar manifest:
the incremental-fetch high-water mark is ``max(last_ts)`` per (symbol,
timeframe). Written ONLY by ``quant.data.store.write_bars``; the backend reads
it and never writes it.

Also adds ``market_stream_symbols`` — the runtime-controlled set of symbols the
realtime 1min consumer subscribes to (lower ``priority`` subscribes first),
kept in the DB rather than ``quant/config.py`` so it can be changed online.

Revision ID: 016
Revises: 015
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '016'
down_revision: Union[str, None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'market_data_files',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('timeframe', sa.String(length=10), nullable=False),
        # daily -> "%Y" | 1hour -> "%Y-%m" | 1min -> "%Y-%m-%d", derived from
        # the bar ts converted to ET (never UTC — post-market crosses midnight)
        sa.Column('period_key', sa.String(length=10), nullable=False),
        # relative to BARS_ROOT: host and container absolute paths differ
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('row_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('first_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_ts', sa.DateTime(timezone=True), nullable=False),
        # sha256 of the CANONICALISED rows + provider, not of the Parquet bytes
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='ok', nullable=False),
        # EOD correction stats: IEX-vs-SIP feed comparison, completeness ratio
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symbol', 'timeframe', 'period_key',
                            name='uq_market_data_file'),
    )
    op.create_index(op.f('ix_market_data_files_symbol_timeframe'),
                    'market_data_files', ['symbol', 'timeframe'], unique=False)
    op.create_index(op.f('ix_market_data_files_provider'),
                    'market_data_files', ['provider'], unique=False)

    op.create_table(
        'market_stream_symbols',
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('priority', sa.Integer(), server_default='100', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('symbol'),
    )


def downgrade() -> None:
    op.drop_table('market_stream_symbols')
    op.drop_index(op.f('ix_market_data_files_provider'),
                  table_name='market_data_files')
    op.drop_index(op.f('ix_market_data_files_symbol_timeframe'),
                  table_name='market_data_files')
    op.drop_table('market_data_files')
