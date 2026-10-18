"""Strategy groups (P1, M1).

A strategy group is one strategy module (``strategy_key`` in
``quant.engine.strategies.REGISTRY``) + one parameter snapshot + one dedicated
system sim account. The group owns its account 1:1, so every ledger row reaches
its group through ``sim_account_id``; only ``recommendations`` carries a
``group_id`` of its own. The composite FK on ``(sim_account_id,
account_is_system)`` keeps a group off practice accounts and stops its account
from being flipped to ``is_system = false``.
"""

from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKeyConstraint, Index, Integer,
    String, Text, UniqueConstraint, func, text
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base

GROUP_STATUSES = ("active", "paused", "archived")
TIMEFRAMES = ("1d", "1m")
# which groups publish recommendations / book entries, and which have their
# open lots managed (exits, stops, protections, snapshots)
SIGNAL_STATUSES = ("active",)
MANAGED_STATUSES = ("active", "paused")
RUN_CYCLES = ("signal", "entry", "position")
RUN_OUTCOMES = ("ok", "skipped", "blocked", "fail_closed", "error")


def _in(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


class StrategyGroup(Base):
    __tablename__ = "strategy_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(100), nullable=False)
    market = Column(String(10), nullable=False, server_default="us")
    strategy_key = Column(String(100), nullable=False)
    params_json = Column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    screener_json = Column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    trade_settings_json = Column(JSONB(), nullable=False,
                                 server_default=text("'{}'::jsonb"))
    risk_json = Column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    timeframe = Column(String(5), nullable=False, server_default="1d")
    status = Column(String(10), nullable=False, server_default="active")
    sim_account_id = Column(UUID(as_uuid=True), nullable=False)
    account_is_system = Column(Boolean(), nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["sim_account_id", "account_is_system"],
                             ["sim_accounts.id", "sim_accounts.is_system"],
                             name="fk_strategy_group_system_account",
                             ondelete="RESTRICT", onupdate="RESTRICT"),
        UniqueConstraint("name", name="uq_strategy_group_name"),
        UniqueConstraint("sim_account_id", name="uq_strategy_group_sim_account"),
        CheckConstraint(_in("status", GROUP_STATUSES), name="ck_strategy_group_status"),
        CheckConstraint(_in("timeframe", TIMEFRAMES), name="ck_strategy_group_timeframe"),
        CheckConstraint("(status = 'archived') = (archived_at IS NOT NULL)",
                        name="ck_strategy_group_archived_at"),
        CheckConstraint("account_is_system", name="ck_strategy_group_account_is_system"),
    )


class StrategyGroupRun(Base):
    """One group's outcome in one cycle run (P3a) — the per-group record the
    shared heartbeat cannot keep."""
    __tablename__ = "strategy_group_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    group_id = Column(UUID(as_uuid=True), nullable=False)
    cycle = Column(String(20), nullable=False)
    session_date = Column(Date, nullable=False)
    seq = Column(Integer, nullable=False, server_default=text("0"))
    outcome = Column(String(20), nullable=False)
    meta = Column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["group_id"], ["strategy_groups.id"],
                             name="fk_group_run_group", ondelete="RESTRICT"),
        CheckConstraint(_in("cycle", RUN_CYCLES), name="ck_group_run_cycle"),
        CheckConstraint(_in("outcome", RUN_OUTCOMES), name="ck_group_run_outcome"),
        Index("ix_strategy_group_runs_group_created", "group_id", "created_at"),
    )
