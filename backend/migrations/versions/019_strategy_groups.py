"""strategy groups (P1): strategy_groups table + recommendations.group_id

A strategy group is one strategy module + one parameter snapshot + one
dedicated system sim account. The pre-group v1 chain becomes group #1 on the
oldest system account, and every existing recommendation is attached to it.

Ledger tables carry no group_id: a group owns exactly one account
(``uq_strategy_group_sim_account``), so orders / positions / fills / snapshots
reach their group through ``sim_account_id``. The composite FK on
``(sim_account_id, account_is_system)`` keeps a group off practice accounts and
stops a group's account from being flipped to ``is_system = false``.

Revision ID: 019
Revises: 018
"""
import json
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '019'
down_revision: Union[str, None] = '018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_V1_KEY = "cat.v1.composite"
_GROUP1_NAME = "v1 对照"
# Kept literal on purpose: a migration must reproduce the same row years later,
# not whatever the registry defaults have drifted to.
_V1_PARAMS = {
    "strategy": {"ma_fast": 5, "ma_slow": 20, "macd_fast": 12, "macd_slow": 26,
                 "macd_signal": 9, "rsi_period": 14, "atr_period": 14,
                 "zscore_window": 60, "rsi_overbought": 70.0,
                 "rsi_penalty_floor": 0.2, "stop_atr_mult": 2.0,
                 "expected_move_atr_mult": 1.0},
    "funnel": {"min_adv": 20000000.0, "min_price": 5.0, "atr_pct_min": 0.01,
               "atr_pct_max": 0.08, "max_per_sector": 2, "shortlist_max": 10,
               "min_confidence": 65.0},
    "exits": {"trailing_start_r": 1.5, "trailing_atr_mult": 3.0,
              "reversal_bars": 3, "stagnation_bars": 30,
              "stagnation_hurdle": 0.0},
}


def upgrade() -> None:
    op.create_unique_constraint('uq_sim_account_id_is_system', 'sim_accounts',
                                ['id', 'is_system'])
    op.create_table(
        'strategy_groups',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('market', sa.String(length=10), server_default='us', nullable=False),
        sa.Column('strategy_key', sa.String(length=100), nullable=False),
        sa.Column('params_json', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('screener_json', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('trade_settings_json', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('risk_json', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('timeframe', sa.String(length=5), server_default='1d', nullable=False),
        sa.Column('status', sa.String(length=10), server_default='active', nullable=False),
        sa.Column('sim_account_id', sa.UUID(), nullable=False),
        sa.Column('account_is_system', sa.Boolean(), server_default=sa.text('true'),
                  nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['sim_account_id', 'account_is_system'],
                                ['sim_accounts.id', 'sim_accounts.is_system'],
                                name='fk_strategy_group_system_account',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_strategy_group_name'),
        sa.UniqueConstraint('sim_account_id', name='uq_strategy_group_sim_account'),
        sa.CheckConstraint("status IN ('active', 'paused', 'archived')",
                           name='ck_strategy_group_status'),
        sa.CheckConstraint("timeframe IN ('1d', '1m')",
                           name='ck_strategy_group_timeframe'),
        sa.CheckConstraint("(status = 'archived') = (archived_at IS NOT NULL)",
                           name='ck_strategy_group_archived_at'),
        sa.CheckConstraint('account_is_system',
                           name='ck_strategy_group_account_is_system'),
    )

    params = json.dumps(_V1_PARAMS, sort_keys=True)
    op.execute(
        "INSERT INTO strategy_groups (id, name, market, strategy_key, params_json, "
        "screener_json, trade_settings_json, risk_json, timeframe, status, "
        "sim_account_id, notes) "
        f"SELECT '{uuid4()}', '{_GROUP1_NAME}', 'us', '{_V1_KEY}', '{params}'::jsonb, "
        "'{}', '{}', '{}', '1d', 'active', a.id, 'pre-group v1 chain migrated by 019' "
        "FROM sim_accounts a WHERE a.is_system ORDER BY a.created_at LIMIT 1")

    op.add_column('recommendations', sa.Column('group_id', sa.UUID(), nullable=True))
    op.execute("UPDATE recommendations SET group_id = "
               f"(SELECT id FROM strategy_groups WHERE strategy_key = '{_V1_KEY}')")
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM recommendations WHERE group_id IS NULL) THEN "
        "RAISE EXCEPTION 'migration 019: recommendations exist but no is_system "
        "sim account owns group #1'; "
        "END IF; END $$")
    op.alter_column('recommendations', 'group_id', nullable=False)
    op.create_foreign_key('fk_recommendation_group', 'recommendations',
                          'strategy_groups', ['group_id'], ['id'], ondelete='RESTRICT')
    op.drop_constraint('uq_recommendation_symbol_date', 'recommendations',
                       type_='unique')
    op.create_unique_constraint('uq_recommendation_group_symbol_date', 'recommendations',
                                ['group_id', 'symbol', 'trade_date'])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF (SELECT count(*) FROM strategy_groups) > 1 THEN "
        "RAISE EXCEPTION 'migration 019 downgrade: more than one strategy group "
        "— would merge their recommendations'; "
        "END IF; END $$")
    op.drop_constraint('uq_recommendation_group_symbol_date', 'recommendations',
                       type_='unique')
    op.create_unique_constraint('uq_recommendation_symbol_date', 'recommendations',
                                ['symbol', 'trade_date'])
    op.drop_constraint('fk_recommendation_group', 'recommendations', type_='foreignkey')
    op.drop_column('recommendations', 'group_id')
    op.drop_table('strategy_groups')
    op.drop_constraint('uq_sim_account_id_is_system', 'sim_accounts', type_='unique')
