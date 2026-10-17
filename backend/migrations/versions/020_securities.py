"""whole-US-equity universe (P2): securities + security_share_facts

``securities`` is one row per listing in the Alpaca active US-equity universe
(OTC excluded), classified by asset type from the Nasdaq Trader symbol
directory; rows that drop out are kept as ``inactive`` with ``inactive_since``.
``security_share_facts`` holds SEC companyconcept share counts with their
``filed`` date, the point-in-time input to market cap. Written only by
``quant.data.securities`` / ``quant.data.edgar``; no existing table changes.

Revision ID: 020
Revises: 019
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '020'
down_revision: Union[str, None] = '019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'securities',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('market', sa.String(length=10), server_default='us', nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=True),
        sa.Column('exchange', sa.String(length=10), nullable=True),
        sa.Column('asset_type', sa.String(length=12), nullable=False),
        sa.Column('status', sa.String(length=10), server_default='active', nullable=False),
        sa.Column('tradable', sa.Boolean(), nullable=False),
        sa.Column('cik', sa.Integer(), nullable=True),
        sa.Column('first_seen', sa.Date(), nullable=False),
        sa.Column('last_seen', sa.Date(), nullable=False),
        sa.Column('inactive_since', sa.Date(), nullable=True),
        sa.Column('shares_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('market', 'code', name='uq_security_market_code'),
        sa.CheckConstraint("status IN ('active', 'inactive')", name='ck_security_status'),
        sa.CheckConstraint("(status = 'inactive') = (inactive_since IS NOT NULL)",
                           name='ck_security_inactive_since'),
        sa.CheckConstraint(
            "asset_type IN ('stock', 'adr', 'etf', 'etn', 'preferred', 'warrant', "
            "'unit', 'right', 'debt', 'test', 'unknown')",
            name='ck_security_asset_type'),
    )
    op.create_index('ix_securities_cik', 'securities', ['cik'], unique=False)

    op.create_table(
        'security_share_facts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('cik', sa.Integer(), nullable=False),
        sa.Column('concept', sa.String(length=80), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('filed', sa.Date(), nullable=False),
        sa.Column('accn', sa.String(length=25), nullable=False),
        sa.Column('form', sa.String(length=12), nullable=True),
        sa.Column('value', sa.BigInteger(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cik', 'concept', 'accn', 'end_date', name='uq_share_fact'),
        sa.CheckConstraint('value > 0', name='ck_share_fact_value'),
    )
    op.create_index('ix_share_facts_cik_filed', 'security_share_facts',
                    ['cik', 'filed'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_share_facts_cik_filed', table_name='security_share_facts')
    op.drop_table('security_share_facts')
    op.drop_index('ix_securities_cik', table_name='securities')
    op.drop_table('securities')
