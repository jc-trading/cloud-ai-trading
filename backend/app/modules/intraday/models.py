"""Intraday watch rows (P5b).

One row per symbol on an active 1m strategy group's shortlist for one session,
written by the nightly ``intraday.nightly`` task (raw SQL from
``quant.data.registry``, which replaces a group's rows for a session as a whole)
and read by the stream resolver and the watch API.
"""

from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, Column, Date, DateTime, ForeignKeyConstraint, Index, Integer, Numeric,
    String, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class IntradayWatch(Base):
    __tablename__ = "intraday_watch"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_date = Column(Date, nullable=False)
    group_id = Column(UUID(as_uuid=True), nullable=False)
    symbol = Column(String(20), nullable=False)
    rank = Column(Integer, nullable=False)
    score = Column(Numeric(12, 6), nullable=True)
    reason = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["group_id"], ["strategy_groups.id"],
                             name="fk_intraday_watch_group", ondelete="RESTRICT"),
        UniqueConstraint("session_date", "group_id", "symbol",
                         name="uq_intraday_watch_session_group_symbol"),
        CheckConstraint("rank >= 1", name="ck_intraday_watch_rank"),
        Index("ix_intraday_watch_session", "session_date"),
    )
