"""Notification channels, routing rules and delivery log (P4, M5).

A channel is one delivery target (a Telegram chat or an SMTP mailbox). Its
secret never lives in the database: ``secret_env_key`` names the environment
variable that holds it, restricted by CHECK to the TELEGRAM_BOT_TOKEN* /
SMTP_APP_PASSWORD* families so an admin cannot point a channel at an unrelated
secret. A rule routes one event type to one channel above a minimum severity,
optionally muted inside quiet hours. Every delivery attempt lands in
``notification_log`` with a snapshot of the channel name.
"""

from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, func, text
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base

CHANNEL_TYPES = ("telegram", "smtp")
SEVERITIES = ("info", "warning", "critical")
LOG_STATUSES = ("sent", "failed", "suppressed")

EVENT_TYPES: dict[str, str] = {
    "fill_entry": "Entries booked for a strategy group",
    "fill_exit": "Positions closed by the daily exit stack or data_end",
    "stop_hit": "Intraday stop exits",
    "data_fail_closed": "A cycle refused to publish or trade because data was missing",
    "data_warning": "Data quality warning that did not stop a cycle",
    "config_rejected": "master_settings rows rejected (only tightening allowed)",
    "daily_summary": "Next-session shortlist",
    "news_high_probability": "High-probability news signal (P6)",
    "source_disabled": "News source auto-disabled by its score (P7)",
    "llm_quota_exhausted": "Claude CLI subscription quota exhausted for the day (M8)",
}

SECRET_ENV_PATTERNS = {
    "telegram": r"^TELEGRAM_BOT_TOKEN(_[A-Z0-9]{1,20})?$",
    "smtp": r"^SMTP_APP_PASSWORD(_[A-Z0-9]{1,20})?$",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    type = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    config_json = Column(JSONB(), nullable=False, server_default=text("'{}'::jsonb"))
    secret_env_key = Column(String(64), nullable=False)
    enabled = Column(Boolean(), nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_notification_channel_name"),
        CheckConstraint(_in("type", CHANNEL_TYPES), name="ck_notification_channel_type"),
        CheckConstraint(
            " OR ".join(f"(type = '{t}' AND secret_env_key ~ '{p}')"
                        for t, p in SECRET_ENV_PATTERNS.items()),
            name="ck_notification_channel_secret_env_key"),
    )


class NotificationRule(Base):
    __tablename__ = "notification_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type = Column(String(50), nullable=False)
    channel_id = Column(UUID(as_uuid=True),
                        ForeignKey("notification_channels.id", ondelete="CASCADE",
                                   name="fk_notification_rule_channel"),
                        nullable=False)
    min_severity = Column(String(10), nullable=False, server_default="info")
    quiet_hours = Column(JSONB(), nullable=True)
    enabled = Column(Boolean(), nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("event_type", "channel_id",
                         name="uq_notification_rule_event_channel"),
        CheckConstraint(_in("min_severity", SEVERITIES),
                        name="ck_notification_rule_min_severity"),
    )


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    channel_id = Column(UUID(as_uuid=True),
                        ForeignKey("notification_channels.id", ondelete="SET NULL",
                                   name="fk_notification_log_channel"),
                        nullable=True)
    channel_name = Column(String(100), nullable=False)
    event_type = Column(String(50), nullable=False)
    severity = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False)
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, server_default="0")
    message_preview = Column(String(200), nullable=True)

    __table_args__ = (
        CheckConstraint(_in("status", LOG_STATUSES), name="ck_notification_log_status"),
        Index("ix_notification_log_sent_at", "sent_at"),
    )
