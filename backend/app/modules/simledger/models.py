"""Internal simulated-trading ledger (R1-3, Direction v3 拍板 2026-07-30).

The v3 platform is simulation-ONLY: no broker orders exist anywhere. Every
account — the system's 对照账户 (is_system=True, driven by the scheduled quant
cycle) and each user's manual practice account — books orders/fills/positions
in THESE tables. Fills price at the live quote adjusted by the same CostModel
the backtest used, and exit management applies the same `quant.engine.exits`
pure functions, so the live scoreboard stays comparable to the backtest.

Tables:
  - sim_accounts       one ledger per (user, purpose); $2,000 default (A2)
  - sim_positions      open/closed lots; fields mirror quant.engine.exits.Position
                       so the pure exit stack can be applied verbatim
  - sim_orders         full lifecycle + idempotency_key (a re-run cycle must
                       never double-book) + reason (entry/hard_stop/trailing/...)
  - sim_fills          one fill per executed order (price after costs)
  - account_snapshots  daily equity curve per account (dashboard vs SPY)
  - safety_state       persisted protections (pause/halt survive restarts)
  - heartbeats         liveness of the scheduled cycles (watchdog reads this)
  - master_settings    §8.6 risk knobs; the service layer only allows TIGHTENING
  - recommendations    the daily engine output feed (v3 dashboard reads this):
                       shortlist rank + confidence + phase read, one per
                       (symbol, trade_date)
"""

from uuid import uuid4

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint, func, text
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class SimAccount(Base):
    __tablename__ = "sim_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    name = Column(String(100), nullable=False, server_default="default")
    is_system = Column(Boolean(), nullable=False, server_default=text("false"))
    starting_capital = Column(Numeric(18, 2), nullable=False, server_default=text("2000"))
    cash = Column(Numeric(18, 2), nullable=False, server_default=text("2000"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_sim_account_user_name"),)


class SimPosition(Base):
    __tablename__ = "sim_positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("sim_accounts.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    status = Column(String(10), nullable=False, server_default="open")   # open|closed

    # mirror of quant.engine.exits.Position so the pure exit stack applies verbatim
    shares = Column(Numeric(18, 6), nullable=False)
    avg_cost = Column(Numeric(18, 6), nullable=False)
    stop = Column(Numeric(18, 6), nullable=False)
    r_unit = Column(Numeric(18, 6), nullable=False)
    high_water = Column(Numeric(18, 6), nullable=False)
    adds_done = Column(Integer, nullable=False, server_default=text("0"))
    reversal_count = Column(Integer, nullable=False, server_default=text("0"))
    bars_held = Column(Integer, nullable=False, server_default=text("0"))
    entry_date = Column(Date, nullable=False)

    opened_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    close_reason = Column(String(30), nullable=True)   # hard_stop|trailing|reversal|...
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    __table_args__ = (
        # at most ONE open lot per (account, symbol) — pyramiding mutates the lot
        Index("uq_sim_position_open", "account_id", "symbol", unique=True,
              postgresql_where=text("status = 'open'")),
    )


class SimOrder(Base):
    __tablename__ = "sim_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("sim_accounts.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    position_id = Column(UUID(as_uuid=True), ForeignKey("sim_positions.id", ondelete="SET NULL"),
                        nullable=True)
    recommendation_id = Column(UUID(as_uuid=True),
                               ForeignKey("recommendations.id", ondelete="SET NULL"),
                               nullable=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(4), nullable=False)              # buy|sell
    qty = Column(Numeric(18, 6), nullable=False)
    order_type = Column(String(10), nullable=False, server_default="market")
    status = Column(String(10), nullable=False, server_default="pending")
    # entry|pyramid|hard_stop|trailing|reversal|stagnation|data_end|manual
    reason = Column(String(30), nullable=False)
    # a re-run of the same cycle must upsert-skip, never double-book
    idempotency_key = Column(String(120), nullable=False, unique=True)
    reject_reason = Column(Text, nullable=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    filled_at = Column(DateTime(timezone=True), nullable=True)


class SimFill(Base):
    __tablename__ = "sim_fills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("sim_orders.id", ondelete="CASCADE"),
                      nullable=False, unique=True)      # sim fills are all-or-nothing
    price = Column(Numeric(18, 6), nullable=False)      # after slippage+spread
    raw_price = Column(Numeric(18, 6), nullable=False)  # quote before costs
    qty = Column(Numeric(18, 6), nullable=False)
    commission = Column(Numeric(18, 6), nullable=False, server_default=text("0"))
    filled_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("sim_accounts.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False)
    equity = Column(Numeric(18, 2), nullable=False)
    cash = Column(Numeric(18, 2), nullable=False)
    open_positions = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("account_id", "snapshot_date",
                                       name="uq_snapshot_account_date"),)


class SafetyState(Base):
    __tablename__ = "safety_state"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    scope = Column(String(50), nullable=False, unique=True)   # 'global' | account uuid
    halted = Column(Boolean(), nullable=False, server_default=text("false"))
    halted_until = Column(DateTime(timezone=True), nullable=True)
    paused_until = Column(Date, nullable=True)                # daily-loss pause
    peak_equity = Column(Numeric(18, 2), nullable=True)
    reason = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


class HeartbeatRecord(Base):
    __tablename__ = "heartbeats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(50), nullable=False, unique=True)    # signal_cycle|position_cycle|...
    last_beat_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    meta = Column(JSONB(), nullable=True)


class MasterSetting(Base):
    __tablename__ = "master_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    key = Column(String(50), nullable=False, unique=True)
    value = Column(Numeric(18, 6), nullable=False)
    # §8.6: runtime changes may only TIGHTEN — enforced in the service layer,
    # recorded here for the audit trail
    tighten_only = Column(Boolean(), nullable=False, server_default=text("true"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol = Column(String(20), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)     # session the rec is FOR
    direction = Column(String(10), nullable=False)            # up|down|flat
    confidence = Column(Numeric(8, 3), nullable=False)
    shortlist_rank = Column(Integer, nullable=True)           # null = not shortlisted
    phase = Column(String(10), nullable=False)                # up|down|range|unknown
    phase_reason = Column(Text, nullable=True)
    # engine evidence for the transparency dashboard (indicators, funnel fields)
    features = Column(JSONB(), nullable=True)
    # one-or-two-sentence LLM read of WHY (explanation-only, does NOT affect the
    # deterministic score); written for top-N names by the signal_cycle
    # explanation layer via app.modules.llm.client. See llm_calls for the cost.
    llm_explanation = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("symbol", "trade_date",
                                       name="uq_recommendation_symbol_date"),)
