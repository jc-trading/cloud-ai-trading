"""P7b non-price judgement: the follow-up headline query, the prompt, response
parsing, and run_judgement against real PostgreSQL (rolled back) with a fake
call_llm — the wait for follow-ups, the no-evidence shortcut, the deadline,
the judge budget, the provider gate — plus one run through the real call_llm
and a fake ``claude`` binary. No real LLM call."""

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import delete, insert, select

from quant.data import calendar as qcal

import app.models_registry  # noqa: F401
from app.config import settings
from app.modules.llm import client as llm_client
from app.modules.llm.client import LLMResult
from app.modules.llm.models import LLMCall
from app.modules.news import evaluate, judge
from app.modules.news.ingest import platform_source
from app.modules.news.judge import JudgeClaim, build_prompt, parse_response
from app.modules.news.models import (
    NewsItem, NewsItemSource, NewsItemSymbol, NewsOutcome, NewsSource, NewsSourceScoreHistory
)
from tests._pg_rollback import pg_session_rollback  # noqa: F401

ANCHOR, END = date(2026, 10, 12), date(2026, 10, 14)
LAST = date(2026, 10, 16)
STATED = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
WINDOW_CLOSE = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 20, 3, 40, tzinfo=timezone.utc)
FOLLOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)
FULL = {ANCHOR: (100.0, 101.0), END: (103.0, 105.0)}
FLAT = {ANCHOR: (100.0, 100.0), END: (100.0, 101.0)}


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    async def no_llm(*a, **kw):
        raise AssertionError("judge.call_llm must be faked explicitly in this test")

    monkeypatch.setattr(settings, "AI_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(judge, "call_llm", no_llm)


def test_build_prompt_exact():
    claim = JudgeClaim("ZQXJA", "Deal &amp; merger\n closes by Q1", STATED, ANCHOR, END,
                       [(FOLLOW, "ZQXJA &#39;on track&#39;"),
                        (WINDOW_CLOSE, "ZQXJA vote at the close"),
                        (datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc), "ZQXJA deal\tcloses")])
    assert build_prompt([claim]) == (
        "Claim 1: ZQXJA | Deal & merger closes by Q1 | stated 2026-10-09 11:00 ET | "
        "window 2026-10-12 to 2026-10-14\n"
        "Follow-up headlines:\n"
        "[1] 2026-10-13 10:00 ET | ZQXJA 'on track'\n"
        "[2] 2026-10-14 16:00 ET | ZQXJA vote at the close\n"
        "[3] 2026-10-15 10:00 ET (after window) | ZQXJA deal closes\n"
        'Return: [{"i":1,"verdict":"happened","evidence":"[2]"}]')


def test_judge_system_bounds_the_event_to_the_window():
    assert judge.JUDGE_SYSTEM.endswith(
        "No prose. An event counts as happened only if it occurred by the window end.")


def test_parse_response():
    body = "```json\n" + json.dumps([
        {"i": 1, "verdict": "happened", "evidence": "[2]"},
        {"i": 2, "verdict": "maybe", "evidence": ""},
        {"i": 3, "verdict": "no_evidence", "evidence": ""},
        {"i": 4, "verdict": "happened"},
        {"i": 5, "verdict": "not_happened", "evidence": "[1]"},
        {"i": 5, "verdict": "happened", "evidence": "[1]"},
    ]) + "\n```"
    assert parse_response(body, 6) == {
        1: ("happened", "[2]"), 2: "invalid: verdict", 3: ("no_evidence", None),
        4: "invalid: evidence", 5: "invalid: duplicate i", 6: "missing"}
    assert parse_response("Sorry.", 2) == {1: "unparsable", 2: "unparsable"}


class FakeLLM:
    """call_llm stand-in: books an llm_calls row like the real one (added, not
    committed) and answers with ``respond(prompt)`` — a text, ("fail", error)
    or ("skip", error)."""

    def __init__(self, respond):
        self.respond = respond
        self.prompts = []
        self.call_ids = []

    async def __call__(self, db, *, context, prompt, system):
        self.prompts.append(prompt)
        answer = self.respond(prompt)
        if isinstance(answer, tuple) and answer[0] == "skip":
            return LLMResult(text=None, call_id=None, input_tokens=0, output_tokens=0,
                             cost_usd=0, success=False, error=answer[1])
        call_id = uuid4()
        failed = isinstance(answer, tuple)
        db.add(LLMCall(id=call_id, context=context, platform="claude-cli", model="haiku",
                       billing="subscription", success=not failed,
                       error=answer[1] if failed else None))
        self.call_ids.append(call_id)
        if failed:
            return LLMResult(text=None, call_id=call_id, input_tokens=0, output_tokens=0,
                             cost_usd=0, success=False, error=answer[1])
        return LLMResult(text=answer, call_id=call_id, input_tokens=10, output_tokens=5,
                         cost_usd=0, success=True)


def _claims_in(prompt: str) -> list[tuple[int, str]]:
    return [(int(m[1]), m[2]) for m in re.finditer(r"^Claim (\d+): (\S+) \|", prompt, re.M)]


def _answer(verdicts: dict, evidence="[1]"):
    """A valid answer: each claim's verdict looked up by its symbol."""
    def respond(prompt):
        return json.dumps([{"i": i, "verdict": verdicts.get(sym, "happened"),
                            "evidence": evidence} for i, sym in _claims_in(prompt)])
    return respond


@pytest_asyncio.fixture
async def db(pg_session_rollback, monkeypatch):
    s = pg_session_rollback
    for key, value in (("AI_PROVIDER", "claude_cli"), ("NEWS_LLM_DAILY_BUDGET", 150),
                       ("NEWS_JUDGE_DAILY_BUDGET", 20), ("CLAUDE_CLI_DAILY_CAP", 300),
                       ("CLAUDE_CLI_TIMEOUT_SECONDS", 60.0),
                       ("NEWS_PRIOR_STRENGTH", 10.0), ("NEWS_PRIOR_MEAN_DEFAULT", 0.40),
                       ("NEWS_PRIOR_MIN_GLOBAL_N", 100), ("NEWS_SOURCE_MIN_N", 20),
                       ("NEWS_HIT_MIN_EXCESS_PCT", 1.0), ("NEWS_NODATA_GRACE_SESSIONS", 5)):
        monkeypatch.setattr(settings, key, value)
    await s.execute(delete(NewsItem))
    await s.execute(delete(NewsSourceScoreHistory))
    src = NewsSource(id=uuid4(), kind="author", name=f"benzinga/ZQXP7B {uuid4().hex[:8]}",
                     parent_id=(await platform_source(s)).id)
    s.add(src)
    await s.flush()
    s.info["source"] = src
    return s


async def _story(db, symbol, at, *, claim=False, evaluate_after=END, origin="live"):
    item = NewsItem(id=uuid4(), origin=origin, headline=f"{symbol} story {uuid4().hex[:6]}",
                    headline_norm=f"zqxp7b {uuid4().hex}", published_at=at)
    db.add(item)
    await db.flush()
    db.add(NewsItemSource(id=uuid4(), news_id=item.id, source_id=db.info["source"].id,
                          provider="alpaca", external_id=f"zqxp7b-{uuid4().hex}",
                          published_at=at))
    fields = dict(claim_type="non_price", claim_text=f"{symbol} deal closes by Q1",
                  direction="up", probability=70, expected_move_pct=2, horizon_days=3,
                  anchor_session=ANCHOR, evaluate_after=evaluate_after,
                  suggested_action="ignore", action_reason="p=70") if claim else {}
    row = NewsItemSymbol(id=uuid4(), news_id=item.id, symbol=symbol, **fields)
    db.add(row)
    await db.flush()
    return SimpleNamespace(item=item, row=row)


async def _claim(db, symbol="ZQXJA", *, followups=1, **kw):
    c = await _story(db, symbol, STATED, claim=True, **kw)
    for k in range(followups):
        await _story(db, symbol, FOLLOW + timedelta(hours=k))
    return c


def _install(monkeypatch, respond) -> FakeLLM:
    fake = FakeLLM(respond)
    monkeypatch.setattr(judge, "call_llm", fake)
    return fake


async def _judge(db, *, last=LAST, now=NOW, prices=None):
    written = []
    out = await judge.run_judgement(db, last=last, now=now,
                                    prices=prices or {"ZQXJA": FULL, "SPY": FLAT},
                                    sector_map={}, written=written)
    return SimpleNamespace(out=out, written=written)


async def _outcomes(db) -> dict:
    return {o.news_symbol_id: o for o in (await db.execute(
        select(NewsOutcome).execution_options(populate_existing=True))).scalars()}


async def _seed_calls(db, n, *, context):
    await db.execute(insert(LLMCall), [
        {"id": uuid4(), "context": context, "platform": "claude-cli", "model": "haiku",
         "billing": "subscription"} for _ in range(n)])


@pytest.mark.asyncio
async def test_j1_no_followups_scores_no_evidence_without_a_call(db, monkeypatch):
    c = await _claim(db, followups=0)
    fake = _install(monkeypatch, _answer({}))
    run = await _judge(db)
    assert fake.prompts == []
    assert (run.out["due"], run.out["no_evidence"], run.out["calls"]) == (1, 1, 0)
    o = (await _outcomes(db))[c.row.id]
    assert (o.status, o.method, o.verdict, o.hit, float(o.score), o.reason, o.llm_call_id) == (
        "scored", "llm", "no_evidence", False, 0.0, None, None)
    assert (o.anchor_session, o.end_session, o.scoring_eligible) == (ANCHOR, END, True)
    assert run.written == [(c.row.id, "ZQXJA", "horizon ended: event no evidence · held")]


@pytest.mark.asyncio
async def test_j2_six_claims_take_two_calls_and_map_back(db, monkeypatch):
    symbols = [f"ZQXJ{k}" for k in "ABCDEF"]
    rows = {s: (await _claim(db, s)).row for s in symbols}
    verdicts = {"ZQXJA": "happened", "ZQXJB": "not_happened", "ZQXJC": "no_evidence",
                "ZQXJD": "happened", "ZQXJE": "not_happened", "ZQXJF": "happened"}
    fake = _install(monkeypatch, _answer(verdicts))
    prices = {s: FULL for s in symbols} | {"SPY": FLAT}
    run = await _judge(db, prices=prices)
    assert [len(_claims_in(p)) for p in fake.prompts] == [5, 1]
    assert (run.out["calls"], run.out["judged"], run.out["invalid"]) == (2, 6, 0)
    call_of = {sym: fake.call_ids[n] for n, p in enumerate(fake.prompts)
               for _, sym in _claims_in(p)}
    assert sorted(call_of) == symbols
    got = await _outcomes(db)
    for s in symbols:
        o = got[rows[s].id]
        happened = verdicts[s] == "happened"
        assert (o.verdict, o.evidence, o.hit, float(o.score)) == (
            verdicts[s], "[1]", happened, 1.0 if happened else 0.0)
        assert o.llm_call_id == call_of[s]
        assert (float(o.anchor_price), float(o.end_price), float(o.excess_move_pct)) == (
            100.0, 105.0, 4.0)
        assert o.reason is None
    assert len(run.written) == 6


@pytest.mark.asyncio
async def test_j3_invalid_answer_is_not_written_and_judged_again(db, monkeypatch):
    first = await _claim(db, "ZQXJA")
    second = await _claim(db, "ZQXJB")

    fake = _install(monkeypatch, _answer({"ZQXJB": "probably"}))
    run = await _judge(db)
    assert (run.out["judged"], run.out["invalid"]) == (1, 1)
    got = await _outcomes(db)
    assert first.row.id in got and second.row.id not in got

    fake.respond = _answer({"ZQXJB": "not_happened"})
    again = await _judge(db)
    assert _claims_in(fake.prompts[1]) == [(1, "ZQXJB")]
    assert (again.out["due"], again.out["judged"]) == (1, 1)
    assert (await _outcomes(db))[second.row.id].verdict == "not_happened"


@pytest.mark.asyncio
async def test_j4_judge_budget_reached_makes_no_call(db, monkeypatch):
    c = await _claim(db)
    await _seed_calls(db, 20, context="news_judgement")
    fake = _install(monkeypatch, _answer({}))
    run = await _judge(db)
    assert fake.prompts == [] and run.out["llm_stop"] == "judge budget reached"
    assert c.row.id not in await _outcomes(db)


@pytest.mark.asyncio
async def test_j5_extraction_calls_do_not_count_against_the_judge_budget(db, monkeypatch):
    c = await _claim(db)
    await _seed_calls(db, 200, context="news_extraction")
    fake = _install(monkeypatch, _answer({}))
    run = await _judge(db)
    assert len(fake.prompts) == 1 and run.out["judged"] == 1
    assert c.row.id in await _outcomes(db)


class Bars:
    def __init__(self, table: dict):
        self.table = table

    def __call__(self, symbol, timeframe, end=None):
        rows = self.table.get(symbol, {})
        df = pd.DataFrame({
            "ts": [pd.Timestamp(d).tz_localize("America/New_York").tz_convert("UTC")
                   for d in rows],
            "open": [o for o, _ in rows.values()],
            "close": [c for _, c in rows.values()],
        })
        df.attrs["unadjusted"] = False
        return df


@pytest.mark.asyncio
async def test_j6_past_the_deadline_is_excluded_and_not_scored(db, monkeypatch):
    c = await _claim(db)
    fake = _install(monkeypatch, _answer({}))

    async def no_held(_db):
        return {}

    async def no_notify(message, *, event, severity):
        raise AssertionError("must not notify")

    monkeypatch.setattr(evaluate, "_notify", no_notify)
    deadline = qcal.advance(END, 10, unit="sessions")
    out = await evaluate.run_evaluation(db, now=NOW, latest_complete=lambda: deadline,
                                        bars_fn=Bars({"ZQXJA": FULL, "SPY": FLAT}),
                                        sectors_fn=dict, relevance_fn=no_held)
    assert fake.prompts == []
    assert (out["judge"]["excluded"], out["judge"]["calls"], out["global_n"]) == (1, 0, 0)
    o = (await _outcomes(db))[c.row.id]
    assert (o.status, o.method, o.reason, o.hit, o.score, o.verdict) == (
        "excluded", "llm", "judge_unavailable", None, None, None)
    src = (await db.execute(select(NewsSource).where(NewsSource.id == db.info["source"].id)
                            .execution_options(populate_existing=True))).scalar_one()
    assert (src.n_evaluated, src.hits) == (0, 0)


@pytest.mark.asyncio
async def test_j6_one_session_before_the_deadline_is_still_judged(db, monkeypatch):
    c = await _claim(db)
    _install(monkeypatch, _answer({}))
    run = await _judge(db, last=qcal.advance(END, 9, unit="sessions"))
    assert (run.out["excluded"], run.out["judged"]) == (0, 1)
    assert (await _outcomes(db))[c.row.id].status == "scored"


@pytest.mark.asyncio
async def test_j7_other_provider_makes_no_call_and_no_write(db, monkeypatch):
    await _claim(db)
    monkeypatch.setattr(settings, "AI_PROVIDER", "deepseek")
    fake = _install(monkeypatch, _answer({}))
    run = await _judge(db)
    assert run.out["llm"] == "skipped: provider not claude_cli" and fake.prompts == []
    assert await _outcomes(db) == {}


@pytest.mark.asyncio
async def test_j8_followups_put_the_window_first_then_the_earliest_grace_period(db):
    c = await _story(db, "ZQXJA", STATED, claim=True)
    inside = [await _story(db, "ZQXJA", WINDOW_CLOSE - timedelta(hours=11 - k)) for k in range(12)]
    grace = [await _story(db, "ZQXJA", WINDOW_CLOSE + timedelta(hours=k + 1)) for k in range(8)]
    excluded = [await _story(db, "ZQXJA", STATED),
                await _story(db, "ZQXJA", STATED - timedelta(minutes=1)),
                await _story(db, "ZQXJA", WINDOW_END + timedelta(seconds=1)),
                await _story(db, "ZQXJB", FOLLOW)]
    got = await judge.followups(db, c.row, c.item, WINDOW_CLOSE, WINDOW_END)
    expected = inside[2:] + grace[:5]
    assert got == [(s.item.published_at, s.item.headline) for s in expected]
    assert inside[-1].item.published_at == WINDOW_CLOSE
    heads = {h for _, h in got}
    assert c.item.headline not in heads
    assert not heads & {s.item.headline for s in excluded}


@pytest.mark.asyncio
async def test_j8_no_in_window_news_gives_fifteen_grace_headlines(db):
    c = await _story(db, "ZQXJC", STATED, claim=True)
    grace = [await _story(db, "ZQXJC", WINDOW_CLOSE + timedelta(hours=k + 1)) for k in range(17)]
    for at in (STATED, STATED - timedelta(minutes=1)):
        await _story(db, "ZQXJC", at)
    await _story(db, "ZQXJD", FOLLOW)
    got = await judge.followups(db, c.row, c.item, WINDOW_CLOSE, WINDOW_END)
    assert got == [(s.item.published_at, s.item.headline) for s in grace[:15]]


@pytest.mark.asyncio
async def test_j9_window_still_collecting_followups_waits(db, monkeypatch):
    c = await _claim(db)
    fake = _install(monkeypatch, _answer({}))
    run = await _judge(db, now=WINDOW_END - timedelta(minutes=1))
    assert (run.out["waiting"], run.out["calls"]) == (1, 0) and fake.prompts == []
    assert c.row.id not in await _outcomes(db)


@pytest.mark.asyncio
async def test_j10_skipped_call_writes_nothing(db, monkeypatch):
    await _claim(db)
    fake = _install(monkeypatch, lambda prompt: ("skip", "claude-cli daily cap reached"))
    run = await _judge(db)
    assert len(fake.prompts) == 1 and run.out["calls"] == 0
    assert run.out["llm_stop"] == "claude-cli daily cap reached"
    assert await _outcomes(db) == {}


@pytest.mark.asyncio
async def test_j11_failed_call_writes_nothing_but_its_row_is_committed(db, monkeypatch):
    await _claim(db)
    fake = _install(monkeypatch, lambda prompt: ("fail", "timeout after 60s"))
    run = await _judge(db)
    assert run.out["llm_stop"] == "timeout after 60s" and run.out["calls"] == 1
    await db.rollback()
    assert await _outcomes(db) == {}
    assert (await db.execute(select(LLMCall.id).where(
        LLMCall.id == fake.call_ids[0]))).scalar_one() == fake.call_ids[0]


@pytest.mark.asyncio
async def test_j12_evidence_over_300_chars_is_invalid(db, monkeypatch):
    ok = await _claim(db, "ZQXJA")
    long = await _claim(db, "ZQXJB")

    def respond(prompt):
        return json.dumps([{"i": i, "verdict": "happened",
                            "evidence": "x" * (300 if sym == "ZQXJA" else 301)}
                           for i, sym in _claims_in(prompt)])

    _install(monkeypatch, respond)
    run = await _judge(db)
    assert (run.out["judged"], run.out["invalid"]) == (1, 1)
    got = await _outcomes(db)
    assert len(got[ok.row.id].evidence) == 300 and long.row.id not in got


_FAKE_CLAUDE = '''#!{python}
import json, sys
sys.stdin.read()
print(json.dumps({{"type": "result", "is_error": False, "result": {answer!r},
                  "total_cost_usd": 0.001,
                  "usage": {{"input_tokens": 2, "cache_creation_input_tokens": 600,
                            "output_tokens": 30}},
                  "modelUsage": {{}}}}))
'''


@pytest.mark.asyncio
async def test_j13_real_call_llm_with_fake_cli(db, monkeypatch, tmp_path):
    c = await _claim(db)
    answer = json.dumps([{"i": 1, "verdict": "happened", "evidence": "[1]"}])
    script = tmp_path / "claude"
    script.write_text(_FAKE_CLAUDE.format(python=sys.executable, answer=answer))
    script.chmod(0o755)
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(settings, key, "")
    monkeypatch.setattr(settings, "CLAUDE_CODE_OAUTH_TOKEN", "oauth-test-token")
    monkeypatch.setattr(settings, "CLAUDE_CLI_PATH", str(script))
    monkeypatch.setattr(settings, "CLAUDE_CLI_MODEL", "haiku")
    monkeypatch.setattr(settings, "CLAUDE_CLI_TIMEOUT_SECONDS", 20.0)
    monkeypatch.setattr(llm_client, "_cli_breaker_until", 0.0)
    monkeypatch.setattr(judge, "call_llm", llm_client.call_llm)

    run = await _judge(db)
    assert (run.out["calls"], run.out["judged"], run.out["llm_stop"]) == (1, 1, None)
    o = (await _outcomes(db))[c.row.id]
    assert (o.verdict, o.hit) == ("happened", True) and o.llm_call_id is not None
    row = (await db.execute(select(LLMCall).where(LLMCall.id == o.llm_call_id))).scalar_one()
    assert (row.context, row.platform, row.billing, row.success) == (
        "news_judgement", "claude-cli", "subscription", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin, eligible", [("live", True), ("backfill", False)])
async def test_q1_llm_outcome_eligibility_follows_the_origin(db, monkeypatch, origin, eligible):
    c = await _claim(db, origin=origin)
    _install(monkeypatch, _answer({}))
    run = await _judge(db)
    assert run.out["judged"] == 1
    assert (await _outcomes(db))[c.row.id].scoring_eligible is eligible


@pytest.mark.asyncio
async def test_q4_skipped_call_stops_the_run_before_the_next_batch(db, monkeypatch):
    for k in "ABCDEF":
        await _claim(db, f"ZQXJ{k}")
    fake = _install(monkeypatch, lambda prompt: ("skip", "claude-cli daily cap reached"))
    run = await _judge(db)
    assert len(fake.prompts) == 1 and run.out["calls"] == 0
    assert await _outcomes(db) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("judge_budget, calls", [(100, 1), (101, 0)])
async def test_q10_news_budgets_must_leave_the_cap_reserve(db, monkeypatch, judge_budget, calls):
    await _claim(db)
    monkeypatch.setattr(settings, "NEWS_JUDGE_DAILY_BUDGET", judge_budget)
    fake = _install(monkeypatch, _answer({}))
    run = await _judge(db)
    assert len(fake.prompts) == calls
    assert run.out["llm"] == ("ok" if calls else "skipped: news budget exceeds cap reserve")


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at, kept", [(1, []), (3, ["ZQXJB"])])
async def test_j16_written_holds_only_committed_outcomes(db, monkeypatch, fail_at, kept):
    await _claim(db, "ZQXJA")
    await _claim(db, "ZQXJB", followups=0)
    _install(monkeypatch, _answer({}))
    real, commits = db.commit, []

    async def flaky():
        commits.append(1)
        if len(commits) == fail_at:
            raise RuntimeError("commit failed")
        await real()

    monkeypatch.setattr(db, "commit", flaky)
    written = []
    with pytest.raises(RuntimeError):
        await judge.run_judgement(db, last=LAST, now=NOW, prices={"ZQXJA": FULL, "SPY": FLAT},
                                  sector_map={}, written=written)
    assert [symbol for _, symbol, _ in written] == kept
