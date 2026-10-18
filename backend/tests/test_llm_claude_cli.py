"""claude_cli provider of call_llm (P4, M8) against a fake ``claude`` binary.

The fake is a Python script that records its argv, environment and stdin and
answers according to a mode file next to it (the child environment is an
allow-list, so the mode cannot travel as an env var). No real CLI, no network,
no DB: the daily-usage query is replaced by a count over the fake session's
added rows.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.modules.llm import client as llm_client
from app.modules.llm.client import build_cli_argv, call_llm, cli_env

_ALLOWED_ENV = {"PATH", "HOME", "LANG", "CLAUDE_CODE_OAUTH_TOKEN", "DISABLE_AUTOUPDATER"}
# variables the interpreter itself may add at start-up (locale coercion, macOS CF)
_INTERPRETER_ENV = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}

_FAKE = '''#!{python}
import json, os, subprocess, sys, time
from pathlib import Path
here = Path(__file__).parent
mode = (here / "mode").read_text().strip()
calls = here / "calls"
calls.write_text(str(int(calls.read_text() or 0) + 1) if calls.exists() else "1")
(here / "record.json").write_text(json.dumps({{
    "argv": sys.argv[1:], "env": dict(os.environ), "stdin": sys.stdin.read()}}))
if mode == "ok":
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
        "result": "Trend is up.", "session_id": "sess-123", "total_cost_usd": 0.0123,
        "usage": {{"input_tokens": 12, "output_tokens": 5, "cache_read_input_tokens": 100,
                   "cache_creation_input_tokens": 7}},
        "modelUsage": {{"claude-haiku-4-5-20251001": {{"costUSD": 0.012}},
                        "claude-tiny": {{"costUSD": 0.0003}}}}}}))
elif mode == "is_error":
    print(json.dumps({{"type": "result", "is_error": True, "session_id": "sess-err",
        "result": "Failed to authenticate. API Error: 401 Invalid bearer token",
        "total_cost_usd": 0, "usage": {{}}, "modelUsage": {{}}}}))
    sys.exit(1)
elif mode == "quota":
    print(json.dumps({{"type": "result", "is_error": True, "session_id": "sess-q",
        "result": "Claude AI usage limit reached|1760000000", "usage": {{}}}}))
    sys.exit(1)
elif mode == "ok_no_model_usage":
    print(json.dumps({{"type": "result", "is_error": False, "result": "Fine.",
        "session_id": "sess-2", "total_cost_usd": 0.002,
        "usage": {{"input_tokens": 3, "output_tokens": 2}}, "modelUsage": {{}}}}))
elif mode == "error_text":
    print(json.dumps({{"type": "result", "is_error": True, "session_id": "sess-t",
        "result": (here / "text").read_text(), "usage": {{}}}}))
    sys.exit(1)
elif mode == "garbage":
    print("Segmentation fault (core dumped)")
    sys.exit(1)
elif mode == "hang":
    child = subprocess.Popen(["sleep", "30"])
    (here / "pids").write_text(f"{{os.getpid()}} {{child.pid}}")
    time.sleep(30)
'''


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(settings, key, "")
    monkeypatch.setattr(llm_client, "_cli_breaker_until", 0.0)


class _Session:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(_FAKE.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setattr(settings, "AI_PROVIDER", "claude_cli")
    monkeypatch.setattr(settings, "CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-token")
    monkeypatch.setattr(settings, "CLAUDE_CLI_PATH", str(script))
    monkeypatch.setattr(settings, "CLAUDE_CLI_MODEL", "haiku")
    monkeypatch.setattr(settings, "CLAUDE_CLI_TIMEOUT_SECONDS", 20.0)
    monkeypatch.setattr(settings, "CLAUDE_CLI_DAILY_CAP", 300)

    async def usage_from_session(db):
        rows = [r for r in db.added if r.platform == "claude-cli"]
        return len(rows), any((r.error or "").startswith("quota_exhausted") for r in rows)

    monkeypatch.setattr(llm_client, "_cli_usage_today", usage_from_session)

    def mode(m, text=""):
        (tmp_path / "mode").write_text(m)
        (tmp_path / "text").write_text(text)

    def calls():
        p = tmp_path / "calls"
        return int(p.read_text()) if p.exists() else 0

    def record():
        return json.loads((tmp_path / "record.json").read_text())

    mode("ok")
    return type("Cli", (), {"dir": tmp_path, "mode": staticmethod(mode),
                            "calls": staticmethod(calls), "record": staticmethod(record)})


def _call(db, **kw):
    kw.setdefault("context", "recommendation_explanation")
    kw.setdefault("prompt", "Explain AAPL.")
    return asyncio.run(call_llm(db, **kw))


def test_success_books_a_subscription_row(cli):
    db = _Session()
    res = _call(db, system="Be brief.", symbol="AAPL")
    assert res.success and res.text == "Trend is up."
    assert (res.input_tokens, res.output_tokens) == (12, 5)
    [row] = db.added
    assert row.platform == "claude-cli"
    assert row.billing == "subscription"
    assert row.model == "claude-haiku-4-5-20251001"
    assert (str(row.unit_price_in), str(row.unit_price_out)) == ("1.0", "5.0")
    assert (row.input_tokens, row.output_tokens) == (12, 5)
    assert (row.cache_read_tokens, row.cache_creation_tokens) == (100, 7)
    assert str(row.cost_usd) == "0.0123" and res.cost_usd == row.cost_usd
    assert row.request_id == "sess-123"
    assert row.success is True and row.error is None
    assert row.symbol == "AAPL" and row.latency_ms >= 0
    assert res.call_id == row.id


@pytest.mark.parametrize("model,price", [("claude-sonnet-5", ("3.0", "15.0")),
                                         ("haiku", ("0", "0"))])
def test_unit_price_falls_back_to_the_requested_model(cli, model, price):
    cli.mode("ok_no_model_usage")
    db = _Session()
    assert _call(db, model=model).success
    [row] = db.added
    assert row.model == model
    assert (str(row.unit_price_in), str(row.unit_price_out)) == price


def test_argv_is_the_slim_command_and_prompt_goes_to_stdin(cli):
    _call(_Session(), prompt="Explain MSFT please.", system="Be brief.")
    rec = cli.record()
    assert [settings.CLAUDE_CLI_PATH] + rec["argv"] == build_cli_argv("haiku", "Be brief.")
    assert rec["argv"].count("") == 2
    assert rec["stdin"] == "Explain MSFT please."
    assert "Explain MSFT please." not in rec["argv"]


def test_default_system_prompt_when_none_given(cli):
    _call(_Session())
    argv = cli.record()["argv"]
    assert argv[argv.index("--system-prompt") + 1] == "Answer concisely."


def test_child_env_is_an_allow_list(cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-auth")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    assert set(cli_env("t")) == _ALLOWED_ENV
    _call(_Session())
    env = cli.record()["env"]
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert set(env) - _INTERPRETER_ENV == _ALLOWED_ENV
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-test-token"
    assert env["DISABLE_AUTOUPDATER"] == "1"


def test_is_error_is_a_failed_row_without_breaker(cli):
    cli.mode("is_error")
    db = _Session()
    res = _call(db)
    assert res.success is False and res.call_id is not None
    [row] = db.added
    assert row.success is False and row.billing == "subscription"
    assert row.error.startswith("claude-cli error: Failed to authenticate")
    assert llm_client._cli_breaker_until == 0.0


def test_unreadable_output_opens_the_breaker(cli):
    cli.mode("garbage")
    db = _Session()
    res = _call(db)
    assert res.success is False
    assert "unreadable output" in db.added[0].error
    assert "exit 1" in db.added[0].error
    assert llm_client._cli_breaker_until > time.monotonic()

    cli.mode("ok")
    again = _call(db)
    assert again.skipped and "circuit breaker" in again.error
    assert cli.calls() == 1
    assert len(db.added) == 1


def test_timeout_kills_the_process_group_and_opens_the_breaker(cli, monkeypatch):
    cli.mode("hang")
    monkeypatch.setattr(settings, "CLAUDE_CLI_TIMEOUT_SECONDS", 1.0)
    db = _Session()
    started = time.monotonic()
    res = _call(db)
    assert time.monotonic() - started < 10
    assert res.success is False
    assert db.added[0].error == "timeout after 1s"

    pids = [int(p) for p in (cli.dir / "pids").read_text().split()]
    deadline = time.monotonic() + 5
    alive = pids
    while alive and time.monotonic() < deadline:
        alive = [p for p in pids if _alive(p)]
        time.sleep(0.05)
    assert alive == []

    again = _call(db)
    assert again.skipped and cli.calls() == 1


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
        return waited == 0
    except ChildProcessError:
        return True


def test_missing_binary_fails_and_opens_the_breaker(cli, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CLI_PATH", str(cli.dir / "no-such-claude"))
    db = _Session()
    res = _call(db)
    assert res.success is False
    assert db.added[0].error.startswith("FileNotFoundError")
    assert llm_client._cli_breaker_until > time.monotonic()


def test_quota_exhaustion_stops_the_day_and_notifies_once(cli, monkeypatch):
    sent = []

    async def fake_notify(event, severity, payload):
        sent.append((event, severity, payload["text"]))
        return True

    import app.modules.notifications.service as notif
    monkeypatch.setattr(notif, "notify", fake_notify)
    cli.mode("quota")
    db = _Session()
    res = _call(db, context="ctx-a")
    assert res.success is False
    assert db.added[0].error.startswith("quota_exhausted: Claude AI usage limit reached")
    assert len(sent) == 1
    event, severity, text = sent[0]
    assert (event, severity) == ("llm_quota_exhausted", "warning")
    assert text.startswith("⚠️ Claude CLI 额度用完") and "context=ctx-a" in text

    again = _call(db)
    assert again.skipped and "quota exhausted today" in again.error
    assert cli.calls() == 1
    assert len(sent) == 1


@pytest.mark.parametrize("text", [
    "Claude AI usage limit reached|1760000000",
    "5-hour limit reached ∙ resets 3pm",
    "You've hit your limit · resets 3pm (Asia/Kuala_Lumpur)",
    "Weekly limit reached",
])
def test_plan_quota_wording_stops_the_day(text):
    assert llm_client._QUOTA_PATTERN.search(text)


@pytest.mark.parametrize("text", [
    "Rate limit reached for requests",
    "Rate-limit reached, retry in 30s",
    "rate_limit reached",
    'API Error: 429 {"type":"error","error":{"type":"rate_limit_error"}}',
    "rate limit exceeded, retry later",
])
def test_transient_rate_limit_is_not_a_quota(text):
    assert not llm_client._QUOTA_PATTERN.search(text)
    assert llm_client._RATE_LIMIT_PATTERN.search(text)


def test_transient_rate_limit_fails_and_trips_only_the_breaker(cli, monkeypatch):
    sent = []

    async def fake_notify(*a, **kw):
        sent.append(a)
        return True

    import app.modules.notifications.service as notif
    monkeypatch.setattr(notif, "notify", fake_notify)
    cli.mode("error_text", "API Error: 429 rate limit exceeded")
    db = _Session()
    res = _call(db)
    assert res.success is False
    assert db.added[0].error == "claude-cli error: API Error: 429 rate limit exceeded"
    assert llm_client._cli_breaker_until > time.monotonic()
    assert sent == []

    again = _call(db)
    assert again.skipped and "circuit breaker" in again.error


def test_daily_cap_skips_without_spawning(cli, monkeypatch):
    async def at_cap(db):
        return 300, False

    monkeypatch.setattr(llm_client, "_cli_usage_today", at_cap)
    db = _Session()
    res = _call(db)
    assert res.skipped and "daily cap" in res.error
    assert cli.calls() == 0 and db.added == []


def test_usage_check_failure_skips(cli, monkeypatch):
    async def broken(db):
        raise RuntimeError("db down")

    monkeypatch.setattr(llm_client, "_cli_usage_today", broken)
    res = _call(_Session())
    assert res.skipped and "usage check failed" in res.error
    assert cli.calls() == 0


def test_empty_token_skips(cli, monkeypatch):
    monkeypatch.setattr(settings, "CLAUDE_CODE_OAUTH_TOKEN", "")
    db = _Session()
    res = _call(db)
    assert res.skipped and res.error == "CLAUDE_CODE_OAUTH_TOKEN not set"
    assert cli.calls() == 0 and db.added == []


def test_usage_check_counts_pending_rows_without_flushing_them():
    """Real PostgreSQL: an invalid pending row (NULL context) must survive the
    usage query unflushed, and still count toward today's usage and quota."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.modules.llm.models import LLMCall

    url = os.environ.get("DATABASE_URL")
    if not url:
        if os.environ.get("CAT_NO_PG") == "1":
            pytest.skip("DATABASE_URL not set")
        pytest.fail("DATABASE_URL not set — run `make test-backend`, or set CAT_NO_PG=1")

    async def run():
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as db:
                base = await llm_client._cli_usage_today(db)
                bad = LLMCall(context=None, platform="claude-cli", model="m",
                              error="quota_exhausted: test")
                db.add(bad)
                after = await llm_client._cli_usage_today(db)
                still_pending = bad in db.new
                await db.rollback()
            return base, after, still_pending
        finally:
            await engine.dispose()

    (n0, _), (n1, quota), still_pending = asyncio.run(run())
    assert still_pending
    assert n1 == n0 + 1 and quota is True
