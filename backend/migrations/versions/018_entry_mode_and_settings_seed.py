"""entry mode per sim account + master_settings seed (Phase C)

Two additive changes, both no-ops on the day they land:

``sim_accounts.entry_mode`` is the INSTANCE's choice of entry semantics —
``open_once`` (D-1 signals filled at D's 09:30 open, the fixed_oos scoreboard's
own behaviour, and the 对照账户 default) or ``intraday_ladder`` (the v3.1
every-15-minutes chase-capped path, kept for a second portfolio instance).

``master_settings`` gets its first rows: the six PLATFORM risk knobs, seeded at
exactly the constants ``quant/config.py`` + ``cycles.RECOMMENDED_FUNNEL`` already
run on, so nothing changes at t0. The read path
(``app.modules.simledger.settings``) only ever accepts a value that TIGHTENS its
knob; a looser row is rejected, logged and alerted, and the constant applies.

Revision ID: 018
Revises: 017
"""
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision: str = '018'
down_revision: Union[str, None] = '017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENTRY_MODES = ("open_once", "intraday_ladder")
_ENTRY_MODE_CK = "ck_sim_account_entry_mode"

# key -> current constant. Kept literal on purpose: a migration must reproduce
# the same rows years later, not whatever the constants have drifted to.
_SEED = (
    ("per_trade_risk_pct", "0.030000"),           # config.PER_TRADE_RISK_PCT
    ("daily_loss_pause_pct", "0.020000"),         # config.DAILY_LOSS_PAUSE_PCT
    ("portfolio_drawdown_halt_pct", "0.150000"),  # config.PORTFOLIO_DRAWDOWN_HALT_PCT
    ("min_confidence", "65.000000"),              # cycles.RECOMMENDED_FUNNEL
    ("intraday_entry_chase_cap", "0.030000"),     # config.INTRADAY_ENTRY_CHASE_CAP
    ("max_concurrent_slots", "10.000000"),        # config.POSITION_LADDER top tier
)


def upgrade() -> None:
    op.add_column('sim_accounts',
                  sa.Column('entry_mode', sa.String(length=20), nullable=False,
                            server_default='open_once'))
    op.create_check_constraint(
        _ENTRY_MODE_CK, 'sim_accounts',
        "entry_mode IN ({})".format(", ".join(f"'{m}'" for m in ENTRY_MODES)))

    values = ", ".join(
        f"('{uuid4()}', '{key}', {value}, true)" for key, value in _SEED)
    op.execute(
        "INSERT INTO master_settings (id, key, value, tighten_only) "
        f"VALUES {values} ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    keys = ", ".join(f"'{key}'" for key, _ in _SEED)
    op.execute(f"DELETE FROM master_settings WHERE key IN ({keys})")
    op.drop_constraint(_ENTRY_MODE_CK, 'sim_accounts', type_='check')
    op.drop_column('sim_accounts', 'entry_mode')
