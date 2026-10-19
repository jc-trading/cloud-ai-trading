"""Backtest runs (P3b).

One row per single or walk-forward backtest of a strategy group. The API only
inserts ``queued`` rows; the research worker claims, runs and finishes them,
so the row is the whole hand-off (no Celery call from the backend). The run
keeps the config snapshot it was queued with, never the group's later edits.
"""

from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKeyConstraint, Index, String,
    Text, func, text
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base

KINDS = ("single", "walk_forward")
STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
TERMINAL = ("succeeded", "failed", "cancelled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    group_id = Column(UUID(as_uuid=True), nullable=False)
    kind = Column(String(20), nullable=False)
    status = Column(String(12), nullable=False, server_default="queued")
    config_snapshot = Column(JSONB(none_as_null=True), nullable=False)
    config_hash = Column(String(64), nullable=False)
    data_start = Column(Date, nullable=False)
    data_end = Column(Date, nullable=False)
    progress = Column(JSONB(none_as_null=True), nullable=False, server_default=text("'{}'::jsonb"))
    cancel_requested = Column(Boolean(), nullable=False, server_default=text("false"))
    metrics_json = Column(JSONB(none_as_null=True), nullable=True)
    calibration_json = Column(JSONB(none_as_null=True), nullable=True)
    artifact_path = Column(Text, nullable=True)
    universe_label = Column(String(40), nullable=False)
    survivorship_biased = Column(Boolean(), nullable=False)
    error = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["group_id"], ["strategy_groups.id"],
                             name="fk_backtest_run_group", ondelete="RESTRICT"),
        ForeignKeyConstraint(["created_by"], ["users.id"],
                             name="fk_backtest_run_user", ondelete="SET NULL"),
        CheckConstraint(_in("kind", KINDS), name="ck_backtest_run_kind"),
        CheckConstraint(_in("status", STATUSES), name="ck_backtest_run_status"),
        CheckConstraint("data_start < data_end", name="ck_backtest_run_range"),
        CheckConstraint(f"({_in('status', TERMINAL)}) = (finished_at IS NOT NULL)",
                        name="ck_backtest_run_finished"),
        CheckConstraint("(status = 'succeeded') = (metrics_json IS NOT NULL)",
                        name="ck_backtest_run_metrics"),
        Index("ix_backtest_runs_group_created", "group_id", "created_at"),
    )
