"""intraday watch rows (P5b)

``intraday_watch`` holds each active 1m strategy group's shortlist for one
session, rebuilt nightly by ``intraday.nightly`` (a group's rows for a session
are replaced as a whole). ``group_id`` is RESTRICT; a symbol appears at most
once per group and session, and ranks start at 1.

Revision ID: 026
Revises: 025
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '026'
down_revision: Union[str, None] = '025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'intraday_watch',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('session_date', sa.Date(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('score', sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['group_id'], ['strategy_groups.id'],
                                name='fk_intraday_watch_group', ondelete='RESTRICT'),
        sa.UniqueConstraint('session_date', 'group_id', 'symbol',
                            name='uq_intraday_watch_session_group_symbol'),
        sa.CheckConstraint('rank >= 1', name='ck_intraday_watch_rank'),
    )
    op.create_index('ix_intraday_watch_session', 'intraday_watch', ['session_date'])


def downgrade() -> None:
    op.drop_index('ix_intraday_watch_session', table_name='intraday_watch')
    op.drop_table('intraday_watch')
