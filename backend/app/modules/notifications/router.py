"""Notification settings API (P4, M5) — admin only.

  GET    /notifications/catalog              event types, severities, channel types
  GET    /notifications/channels             channels + whether their secret is set
  POST   /notifications/channels             create a channel
  PATCH  /notifications/channels/{id}        update (type is immutable)
  DELETE /notifications/channels/{id}        delete (its rules cascade)
  POST   /notifications/channels/{id}/test   send a test message now
  GET    /notifications/rules                rules with their channel name
  POST   /notifications/rules                create a rule (one per event+channel)
  PATCH  /notifications/rules/{id}           update severity / quiet hours / enabled
  DELETE /notifications/rules/{id}
  GET    /notifications/log?limit=50         most recent deliveries

Secrets never pass through this API: a channel stores only the NAME of the env
variable holding its secret, and responses report ``secret_present`` alone.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timezone
from functools import cache
from typing import Any, Optional
from uuid import UUID
from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.dependencies import DB, AdminUser
from app.modules.notifications.channels import _env
from app.modules.notifications.models import (
    CHANNEL_TYPES, EVENT_TYPES, LOG_STATUSES, SECRET_ENV_PATTERNS, SEVERITIES,
    NotificationChannel, NotificationLog, NotificationRule,
)
from app.modules.notifications.service import send_test

router = APIRouter(prefix="/notifications", tags=["Notifications"])

_CHAT_ID = re.compile(r"-?\d{1,20}|@[A-Za-z0-9_]{5,32}", re.ASCII)
_CHAT_ID_ENV = re.compile(r"TELEGRAM_CHAT_ID(_[A-Z0-9]{1,20})?")
_SMTP_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")
_SMTP_USER_ENV = re.compile(r"SMTP_USER(_[A-Z0-9]{1,20})?")
_HHMM = re.compile(r"\d\d:\d\d", re.ASCII)


@cache
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TelegramConfig(_Strict):
    chat_id: Optional[str] = None
    chat_id_env: Optional[str] = None

    @model_validator(mode="after")
    def _one_target(self):
        if (self.chat_id is None) == (self.chat_id_env is None):
            raise ValueError("set exactly one of chat_id or chat_id_env")
        if self.chat_id is not None and not _CHAT_ID.fullmatch(self.chat_id):
            raise ValueError("chat_id must be a numeric id or @channelname")
        if self.chat_id_env is not None and not _CHAT_ID_ENV.fullmatch(self.chat_id_env):
            raise ValueError("chat_id_env must match TELEGRAM_CHAT_ID[_SUFFIX]")
        return self


class SmtpConfig(_Strict):
    host: str
    port: int = Field(ge=1, le=65535)
    username_env: str
    to: list[EmailStr] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _names(self):
        if not _SMTP_HOST.fullmatch(self.host):
            raise ValueError("host must be a hostname")
        if not _SMTP_USER_ENV.fullmatch(self.username_env):
            raise ValueError("username_env must match SMTP_USER[_SUFFIX]")
        return self


_CONFIG_MODELS = {"telegram": TelegramConfig, "smtp": SmtpConfig}


class QuietHours(_Strict):
    start: str
    end: str
    tz: str

    @model_validator(mode="after")
    def _window(self):
        for value in (self.start, self.end):
            if not _HHMM.fullmatch(value):
                raise ValueError("start and end must be HH:MM")
            try:
                time.fromisoformat(value)
            except ValueError:
                raise ValueError(f"{value} is not a valid time") from None
        if self.start == self.end:
            raise ValueError("start and end must differ")
        if self.tz not in _timezones():
            raise ValueError(f"unknown time zone {self.tz!r}")
        return self


class ChannelCreate(_Strict):
    type: str
    name: str = Field(min_length=1, max_length=100)
    config_json: dict[str, Any] = Field(default_factory=dict)
    secret_env_key: str
    enabled: bool = True


class ChannelUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    config_json: Optional[dict[str, Any]] = None
    secret_env_key: Optional[str] = None
    enabled: Optional[bool] = None


class RuleCreate(_Strict):
    event_type: str
    channel_id: UUID
    min_severity: str = "info"
    quiet_hours: Optional[QuietHours] = None
    enabled: bool = True


class RuleUpdate(_Strict):
    min_severity: Optional[str] = None
    quiet_hours: Optional[QuietHours] = None
    enabled: Optional[bool] = None


def _unprocessable(msg: str, loc: str) -> HTTPException:
    return HTTPException(status_code=422, detail=[{"loc": ["body", loc], "msg": msg}])


def _validate_channel(type_: str, config: dict, secret_env_key: str) -> dict:
    if type_ not in CHANNEL_TYPES:
        raise _unprocessable(f"type must be one of {', '.join(CHANNEL_TYPES)}", "type")
    if not re.fullmatch(SECRET_ENV_PATTERNS[type_], secret_env_key):
        raise _unprocessable(
            f"secret_env_key must match {SECRET_ENV_PATTERNS[type_]} for {type_}",
            "secret_env_key")
    try:
        model = _CONFIG_MODELS[type_].model_validate(config)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=[
            {"loc": ["body", "config_json", *err["loc"]], "msg": err["msg"]}
            for err in e.errors()]) from None
    return model.model_dump(mode="json", exclude_none=True)


def _validate_severity(value: str) -> None:
    if value not in SEVERITIES:
        raise _unprocessable(f"min_severity must be one of {', '.join(SEVERITIES)}",
                             "min_severity")


def _channel_out(c: NotificationChannel) -> dict:
    return {
        "id": str(c.id), "type": c.type, "name": c.name, "config_json": c.config_json,
        "secret_env_key": c.secret_env_key, "secret_present": bool(_env(c.secret_env_key)),
        "enabled": c.enabled,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _rule_out(r: NotificationRule, channel_name: str) -> dict:
    return {
        "id": str(r.id), "event_type": r.event_type, "channel_id": str(r.channel_id),
        "channel_name": channel_name, "min_severity": r.min_severity,
        "quiet_hours": r.quiet_hours, "enabled": r.enabled,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _get_channel(db, channel_id: UUID) -> NotificationChannel:
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return channel


async def _get_rule(db, rule_id: UUID) -> NotificationRule:
    rule = await db.get(NotificationRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return rule


async def _flush_or_conflict(db, detail: str) -> None:
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=detail) from None


@router.get("/catalog")
async def catalog(admin: AdminUser):
    return {
        "event_types": [{"key": k, "description": v} for k, v in EVENT_TYPES.items()],
        "severities": list(SEVERITIES),
        "channel_types": list(CHANNEL_TYPES),
        "log_statuses": list(LOG_STATUSES),
        "secret_env_patterns": SECRET_ENV_PATTERNS,
    }


@router.get("/channels")
async def list_channels(admin: AdminUser, db: DB):
    rows = (await db.execute(
        select(NotificationChannel).order_by(NotificationChannel.created_at,
                                             NotificationChannel.name)
    )).scalars().all()
    return [_channel_out(c) for c in rows]


@router.post("/channels", status_code=201)
async def create_channel(data: ChannelCreate, admin: AdminUser, db: DB):
    config = _validate_channel(data.type, data.config_json, data.secret_env_key)
    channel = NotificationChannel(type=data.type, name=data.name, config_json=config,
                                  secret_env_key=data.secret_env_key, enabled=data.enabled)
    db.add(channel)
    await _flush_or_conflict(db, f"channel {data.name!r} already exists")
    await db.refresh(channel)
    return _channel_out(channel)


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: UUID, data: ChannelUpdate, admin: AdminUser, db: DB):
    channel = await _get_channel(db, channel_id)
    config = data.config_json if data.config_json is not None else channel.config_json
    secret = data.secret_env_key if data.secret_env_key is not None else channel.secret_env_key
    channel.config_json = _validate_channel(channel.type, config, secret)
    channel.secret_env_key = secret
    if data.name is not None:
        channel.name = data.name
    if data.enabled is not None:
        channel.enabled = data.enabled
    channel.updated_at = datetime.now(timezone.utc)
    await _flush_or_conflict(db, f"channel {channel.name!r} already exists")
    await db.refresh(channel)
    return _channel_out(channel)


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: UUID, admin: AdminUser, db: DB):
    await db.delete(await _get_channel(db, channel_id))
    return Response(status_code=204)


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: UUID, admin: AdminUser, db: DB):
    outcome = await send_test(db, await _get_channel(db, channel_id))
    return {"status": "sent" if outcome.ok else "failed",
            "attempts": outcome.attempts, "error": outcome.error}


@router.get("/rules")
async def list_rules(admin: AdminUser, db: DB):
    rows = (await db.execute(
        select(NotificationRule, NotificationChannel.name)
        .join(NotificationChannel, NotificationChannel.id == NotificationRule.channel_id)
        .order_by(NotificationRule.event_type, NotificationChannel.name)
    )).all()
    return [_rule_out(r, name) for r, name in rows]


@router.post("/rules", status_code=201)
async def create_rule(data: RuleCreate, admin: AdminUser, db: DB):
    if data.event_type not in EVENT_TYPES:
        raise _unprocessable(f"unknown event_type {data.event_type!r}", "event_type")
    _validate_severity(data.min_severity)
    channel = await _get_channel(db, data.channel_id)
    rule = NotificationRule(
        event_type=data.event_type, channel_id=channel.id, min_severity=data.min_severity,
        quiet_hours=data.quiet_hours.model_dump() if data.quiet_hours else None,
        enabled=data.enabled)
    db.add(rule)
    await _flush_or_conflict(
        db, f"a rule for {data.event_type} on {channel.name!r} already exists")
    await db.refresh(rule)
    return _rule_out(rule, channel.name)


@router.patch("/rules/{rule_id}")
async def update_rule(rule_id: UUID, data: RuleUpdate, admin: AdminUser, db: DB):
    rule = await _get_rule(db, rule_id)
    if data.min_severity is not None:
        _validate_severity(data.min_severity)
        rule.min_severity = data.min_severity
    if "quiet_hours" in data.model_fields_set:
        rule.quiet_hours = data.quiet_hours.model_dump() if data.quiet_hours else None
    if data.enabled is not None:
        rule.enabled = data.enabled
    await db.flush()
    channel = await _get_channel(db, rule.channel_id)
    return _rule_out(rule, channel.name)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: UUID, admin: AdminUser, db: DB):
    await db.delete(await _get_rule(db, rule_id))
    return Response(status_code=204)


@router.get("/log")
async def delivery_log(admin: AdminUser, db: DB,
                       limit: int = Query(default=50, ge=1, le=500)):
    rows = (await db.execute(
        select(NotificationLog)
        .order_by(NotificationLog.sent_at.desc(), NotificationLog.id).limit(limit)
    )).scalars().all()
    return [{
        "id": str(r.id), "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        "channel_id": str(r.channel_id) if r.channel_id else None,
        "channel_name": r.channel_name, "event_type": r.event_type,
        "severity": r.severity, "status": r.status, "error": r.error,
        "retry_count": r.retry_count, "message_preview": r.message_preview,
    } for r in rows]
