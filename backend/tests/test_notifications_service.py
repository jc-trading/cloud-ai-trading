"""notify() routing, quiet hours, legacy fallback and delivery logging (P4, M5).

Standalone — no DB, no network. The Celery session factory is replaced by a
fake that serves (rule, channel) pairs and records committed log rows; the
Telegram client is replaced by a recorder, so nothing is ever sent.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import celery_database
from app.config import settings
from app.modules.notifications import channels as ch
from app.modules.notifications import service, telegram
from app.modules.notifications.channels import SendOutcome
from app.modules.notifications.models import NotificationLog


@pytest.fixture(autouse=True)
def _no_real_secrets(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(settings, key, "")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, factory):
        self.factory = factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        if self.factory.read_error:
            raise self.factory.read_error
        return _Result(self.factory.pairs)

    def add_all(self, rows):
        self.factory.pending.extend(rows)

    async def commit(self):
        if self.factory.commit_error:
            raise self.factory.commit_error
        self.factory.logs.extend(self.factory.pending)
        self.factory.pending = []


class _Factory:
    def __init__(self, pairs=(), read_error=None, commit_error=None, raise_on_open=None):
        self.pairs = list(pairs)
        self.read_error = read_error
        self.commit_error = commit_error
        self.raise_on_open = raise_on_open
        self.pending = []
        self.logs = []

    def __call__(self):
        if self.raise_on_open:
            raise self.raise_on_open
        return _Session(self)


class _HangingSession(_Session):
    """A half-open database: connecting (or committing) never returns."""

    def __init__(self, factory, hang_on):
        super().__init__(factory)
        self.hang_on = hang_on

    async def __aenter__(self):
        if self.hang_on == "open":
            await asyncio.sleep(3600)
        return self

    async def commit(self):
        if self.hang_on == "commit":
            await asyncio.sleep(3600)
        await super().commit()


class _HangingFactory(_Factory):
    def __init__(self, hang_on, pairs=()):
        super().__init__(pairs)
        self.hang_on = hang_on

    def __call__(self):
        return _HangingSession(self, self.hang_on)


class _FakeTelegram:
    """Records every construction and message; outcome scripted per class."""
    instances = []
    ok = True
    attempts = 1
    error = None

    def __init__(self, bot_token=None, chat_id=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.sent = []
        self.last_attempts = 0
        self.last_error = None
        _FakeTelegram.instances.append(self)

    async def send_message(self, message, parse_mode="Markdown"):
        self.sent.append((message, parse_mode))
        self.last_attempts = _FakeTelegram.attempts
        self.last_error = _FakeTelegram.error
        return _FakeTelegram.ok


@pytest.fixture
def tg(monkeypatch):
    _FakeTelegram.instances = []
    _FakeTelegram.ok = True
    _FakeTelegram.attempts = 1
    _FakeTelegram.error = None
    monkeypatch.setattr(telegram, "TelegramNotifier", _FakeTelegram)
    return _FakeTelegram


def _channel(name="telegram-default", type_="telegram", secret="TELEGRAM_BOT_TOKEN",
             config=None):
    return SimpleNamespace(id=uuid4(), name=name, type=type_, secret_env_key=secret,
                           config_json=config if config is not None
                           else {"chat_id_env": "TELEGRAM_CHAT_ID"})


def _rule(min_severity="info", quiet_hours=None):
    return SimpleNamespace(min_severity=min_severity, quiet_hours=quiet_hours)


def _use(monkeypatch, factory):
    monkeypatch.setattr(celery_database, "CeleryAsyncSessionLocal", factory)
    return factory


def _notify(event="stop_hit", severity="warning", text="hello"):
    return asyncio.run(service.notify(event, severity, {"text": text}))


def _bounded_notify(text="hello"):
    """notify() under the test's own 3 s cap, so a missing production timeout
    fails fast instead of hanging on the fake's hour-long sleep."""
    return asyncio.run(asyncio.wait_for(
        service.notify("stop_hit", "warning", {"text": text}), timeout=3))


# ---- pure helpers ----------------------------------------------------------


@pytest.mark.parametrize("sev,minimum,ok", [
    ("info", "info", True), ("info", "warning", False), ("warning", "warning", True),
    ("warning", "critical", False), ("critical", "info", True), ("critical", "critical", True),
])
def test_severity_threshold(sev, minimum, ok):
    assert service.severity_passes(sev, minimum) is ok


def _utc(h, m=0):
    return datetime(2026, 10, 19, h, m, tzinfo=timezone.utc)


@pytest.mark.parametrize("qh,now,quiet", [
    ({"start": "09:00", "end": "17:00", "tz": "UTC"}, _utc(12), True),
    ({"start": "09:00", "end": "17:00", "tz": "UTC"}, _utc(17), False),
    ({"start": "09:00", "end": "17:00", "tz": "UTC"}, _utc(8, 59), False),
    ({"start": "22:00", "end": "06:00", "tz": "UTC"}, _utc(23), True),
    ({"start": "22:00", "end": "06:00", "tz": "UTC"}, _utc(5, 59), True),
    ({"start": "22:00", "end": "06:00", "tz": "UTC"}, _utc(6), False),
    ({"start": "22:00", "end": "06:00", "tz": "UTC"}, _utc(12), False),
    # 15:00 UTC = 23:00 in Kuala Lumpur (UTC+8)
    ({"start": "22:00", "end": "06:00", "tz": "Asia/Kuala_Lumpur"}, _utc(15), True),
    ({"start": "22:00", "end": "06:00", "tz": "Asia/Kuala_Lumpur"}, _utc(23), False),
    ({"start": "xx", "end": "06:00", "tz": "UTC"}, _utc(23), False),
    ({"start": "22:00", "end": "06:00", "tz": "Not/AZone"}, _utc(23), False),
    ({"start": "22:00"}, _utc(23), False),
    (None, _utc(23), False),
])
def test_quiet_hours(qh, now, quiet):
    assert service.in_quiet_hours(qh, now) is quiet


# ---- routing -------------------------------------------------------------------


def test_parity_default_channel_sends_env_token_and_exact_text(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-default")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    factory = _use(monkeypatch, _Factory([(_rule(), _channel())]))
    text = "[组] 🛑 对照账户 stop exit: AAA, B_B *x*"

    assert _notify(text=text) is True
    assert len(tg.instances) == 1
    n = tg.instances[0]
    assert (n.bot_token, n.chat_id) == ("tok-default", "12345")
    assert n.sent == [(text, None)]
    [row] = factory.logs
    assert isinstance(row, NotificationLog)
    assert (row.status, row.channel_name, row.event_type, row.severity) == (
        "sent", "telegram-default", "stop_hit", "warning")
    assert (row.retry_count, row.error, row.message_preview) == (0, None, text)


def test_secondary_channel_never_borrows_the_default_token(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-default")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    factory = _use(monkeypatch, _Factory(
        [(_rule(), _channel(name="second", secret="TELEGRAM_BOT_TOKEN_2"))]))

    assert _notify() is False
    assert tg.instances == []
    [row] = factory.logs
    assert row.status == "failed"
    assert "TELEGRAM_BOT_TOKEN_2 not set" in row.error


def test_missing_chat_id_fails_without_sending(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    factory = _use(monkeypatch, _Factory([(_rule(), _channel())]))
    assert _notify() is False
    assert tg.instances == []
    assert factory.logs[0].error == "telegram chat id not set"


def test_severity_below_rule_minimum_is_skipped_silently(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    factory = _use(monkeypatch, _Factory([(_rule(min_severity="critical"), _channel())]))
    assert _notify(severity="warning") is False
    assert tg.instances == []
    assert factory.logs == []


def test_quiet_hours_suppress_non_critical_and_log_it(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    always_quiet = {"start": "00:00", "end": "23:59", "tz": "UTC"}
    monkeypatch.setattr(service, "in_quiet_hours", lambda qh, now: qh is always_quiet)
    factory = _use(monkeypatch, _Factory([(_rule(quiet_hours=always_quiet), _channel())]))

    assert _notify(severity="warning") is False
    assert tg.instances == []
    assert [r.status for r in factory.logs] == ["suppressed"]


def test_critical_pierces_quiet_hours(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(service, "in_quiet_hours", lambda qh, now: True)
    factory = _use(monkeypatch, _Factory(
        [(_rule(quiet_hours={"start": "00:00", "end": "23:59", "tz": "UTC"}), _channel())]))

    assert _notify(event="data_fail_closed", severity="critical") is True
    assert len(tg.instances) == 1
    assert [r.status for r in factory.logs] == ["sent"]


def test_malformed_quiet_hours_still_send(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    factory = _use(monkeypatch, _Factory([(_rule(quiet_hours={"start": "bad"}), _channel())]))
    assert _notify(severity="info", event="daily_summary") is True
    assert [r.status for r in factory.logs] == ["sent"]


def test_any_channel_success_is_true_and_each_delivery_is_logged(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    good = _channel()
    bad = _channel(name="gmail", type_="smtp", secret="SMTP_APP_PASSWORD",
                   config={"host": "h", "port": 587, "username_env": "SMTP_USER", "to": []})
    factory = _use(monkeypatch, _Factory([(_rule(), good), (_rule(), bad)]))

    assert _notify() is True
    by_name = {r.channel_name: r for r in factory.logs}
    assert by_name["telegram-default"].status == "sent"
    assert by_name["gmail"].status == "failed"
    assert by_name["gmail"].error == "SMTP_USER not set"


def test_all_channels_failing_is_false(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    tg.ok, tg.attempts, tg.error = False, 4, "HTTP 502: bad gateway"
    factory = _use(monkeypatch, _Factory([(_rule(), _channel())]))

    assert _notify() is False
    [row] = factory.logs
    assert (row.status, row.retry_count, row.error) == ("failed", 3, "HTTP 502: bad gateway")


def test_adapter_exception_becomes_a_failed_delivery(monkeypatch, tg):
    async def boom(*_a, **_kw):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(service, "send_via", boom)
    factory = _use(monkeypatch, _Factory([(_rule(), _channel())]))
    assert _notify() is False
    [row] = factory.logs
    assert row.status == "failed" and "adapter exploded" in row.error and row.retry_count == 0


def test_duplicate_channel_pairs_send_once(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    c = _channel()
    factory = _use(monkeypatch, _Factory([(_rule(), c), (_rule(), c)]))
    assert _notify() is True
    assert len(tg.instances) == 1
    assert len(factory.logs) == 1


def test_log_write_failure_is_swallowed(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    _use(monkeypatch, _Factory([(_rule(), _channel())],
                               commit_error=RuntimeError("log table gone")))
    assert _notify() is True


# ---- legacy fallback -----------------------------------------------------------


def test_unreadable_rules_fall_back_to_legacy_exactly_once(monkeypatch, tg):
    _use(monkeypatch, _Factory(raise_on_open=RuntimeError('relation "notification_rules" '
                                                          "does not exist")))
    assert _notify(text="⛔ x") is True
    assert len(tg.instances) == 1
    n = tg.instances[0]
    assert (n.bot_token, n.chat_id) == (None, None)
    assert n.sent == [("⛔ x", None)]


def test_query_error_falls_back_to_legacy(monkeypatch, tg):
    _use(monkeypatch, _Factory(read_error=RuntimeError("db down")))
    assert _notify() is True
    assert len(tg.instances) == 1


@pytest.mark.parametrize("event,severity", [("not_an_event", "info"), ("stop_hit", "loud")])
def test_unknown_event_or_severity_goes_legacy_without_reading_rules(monkeypatch, tg,
                                                                     event, severity):
    _use(monkeypatch, _Factory(raise_on_open=AssertionError("rules must not be read")))
    assert _notify(event=event, severity=severity) is True
    assert len(tg.instances) == 1


def test_legacy_failure_returns_false(monkeypatch, tg):
    tg.ok = False
    _use(monkeypatch, _Factory(raise_on_open=RuntimeError("no table")))
    assert _notify() is False


def test_empty_text_sends_nothing(monkeypatch, tg):
    _use(monkeypatch, _Factory(raise_on_open=AssertionError("must not run")))
    assert asyncio.run(service.notify("stop_hit", "warning", {})) is False
    assert asyncio.run(service.notify("stop_hit", "warning", {"text": ""})) is False
    assert tg.instances == []


def test_half_open_database_falls_back_to_legacy_quickly(monkeypatch, tg):
    monkeypatch.setattr(service, "ROUTES_TIMEOUT_SECONDS", 0.2)
    _use(monkeypatch, _HangingFactory("open"))
    started = time.monotonic()
    assert _bounded_notify(text="⛔ db wedged") is True
    assert time.monotonic() - started < 2
    assert len(tg.instances) == 1 and tg.instances[0].sent == [("⛔ db wedged", None)]


def test_routes_timeout_default_is_five_seconds():
    assert service.ROUTES_TIMEOUT_SECONDS == 5


def test_hanging_log_write_does_not_stall_the_caller(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(service, "ROUTES_TIMEOUT_SECONDS", 0.2)
    _use(monkeypatch, _HangingFactory("commit", [(_rule(), _channel())]))
    started = time.monotonic()
    assert _bounded_notify() is True
    assert time.monotonic() - started < 2


# ---- critical safety net -------------------------------------------------------


def test_critical_with_no_route_falls_back_to_legacy_once(monkeypatch, tg):
    factory = _use(monkeypatch, _Factory([]))
    assert _notify(event="data_fail_closed", severity="critical", text="⛔ x") is True
    assert len(tg.instances) == 1
    assert (tg.instances[0].bot_token, tg.instances[0].chat_id) == (None, None)
    [row] = factory.logs
    assert (row.channel_id, row.channel_name, row.status, row.severity) == (
        None, service.LEGACY_FALLBACK_CHANNEL, "sent", "critical")


def test_critical_with_every_channel_failing_falls_back_to_legacy(monkeypatch, tg):
    factory = _use(monkeypatch, _Factory(
        [(_rule(), _channel(name="second", secret="TELEGRAM_BOT_TOKEN_2"))]))
    tg.ok = False
    assert _notify(event="data_fail_closed", severity="critical") is False
    assert len(tg.instances) == 1
    assert [(r.channel_name, r.status) for r in factory.logs] == [
        ("second", "failed"), (service.LEGACY_FALLBACK_CHANNEL, "failed")]


def test_critical_delivered_by_a_channel_does_not_double_send(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    factory = _use(monkeypatch, _Factory([(_rule(), _channel())]))
    assert _notify(event="data_fail_closed", severity="critical") is True
    assert len(tg.instances) == 1
    assert [r.channel_name for r in factory.logs] == ["telegram-default"]


def test_critical_not_retried_when_the_default_telegram_itself_failed(monkeypatch, tg,
                                                                       caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    tg.ok = False
    explicit_same_chat = _channel(name="same-chat", config={"chat_id": "12345"})
    factory = _use(monkeypatch, _Factory([(_rule(), _channel()),
                                          (_rule(), explicit_same_chat)]))
    with caplog.at_level("WARNING"):
        assert _notify(event="data_fail_closed", severity="critical") is False
    assert len(tg.instances) == 2
    assert all(n.bot_token == "tok" for n in tg.instances)
    assert [r.channel_name for r in factory.logs] == ["telegram-default", "same-chat"]
    assert "legacy fallback skipped" in caplog.text


def test_critical_retried_when_the_failed_default_points_at_another_chat(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    tg.ok = False
    other_chat = _channel(name="other-chat", config={"chat_id": "-100999"})
    factory = _use(monkeypatch, _Factory([(_rule(), other_chat)]))
    assert _notify(event="data_fail_closed", severity="critical") is False
    assert len(tg.instances) == 2
    assert tg.instances[1].bot_token is None
    assert [r.channel_name for r in factory.logs] == [
        "other-chat", service.LEGACY_FALLBACK_CHANNEL]


@pytest.mark.parametrize("severity", ["info", "warning"])
def test_non_critical_with_no_route_stays_silent(monkeypatch, tg, severity):
    factory = _use(monkeypatch, _Factory([]))
    assert _notify(event="daily_summary", severity=severity) is False
    assert tg.instances == []
    assert factory.logs == []


# ---- send_test -----------------------------------------------------------------


def test_send_test_ignores_rules_and_adds_a_log_row(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    added = []
    db = SimpleNamespace(add=added.append)

    outcome = asyncio.run(service.send_test(db, _channel()))
    assert outcome == SendOutcome(True, 1, None)
    [(text, mode)] = tg.instances[0].sent
    assert text.startswith("✅ CAT notification test — telegram-default (telegram) at ")
    assert mode is None
    [row] = added
    assert (row.event_type, row.severity, row.status) == ("test", "info", "sent")


def test_env_lookup_falls_back_to_settings(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "from-settings")
    assert ch._env("TELEGRAM_BOT_TOKEN") == "from-settings"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    assert ch._env("TELEGRAM_BOT_TOKEN") == "from-env"
    assert ch._env("TELEGRAM_BOT_TOKEN_9") == ""
