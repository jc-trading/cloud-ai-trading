"""P6b news extraction: prompt text, response parsing, and run_extraction
against real PostgreSQL (rolled back) with a fake call_llm — plus one run
through the real call_llm and a fake ``claude`` binary. No real LLM call."""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select, text

import app.models_registry  # noqa: F401
from app.config import settings
from app.modules.llm import client as llm_client
from app.modules.llm.client import LLMResult
from app.modules.llm.models import LLMCall
from app.modules.news import extract
from app.modules.news.extract import (
    EXTRACT_SYSTEM, ItemOut, PromptItem, build_prompt, parse_response
)
from app.modules.news.models import NewsItem, NewsItemSource, NewsItemSymbol, NewsSource
from app.modules.news.rules import hit_min_excess, next_open_session
from tests._pg_rollback import pg_session_rollback  # noqa: F401

ET = ZoneInfo("America/New_York")
CLOCK = datetime(2026, 10, 16, 19, 0, tzinfo=timezone.utc)
PUB = datetime(2026, 10, 16, 12, 5, tzinfo=timezone.utc)
ITEMS = [PromptItem(PUB, ["ZQXA", "ZQXB"], "ZQXA beats estimates", "Revenue up 20%."),
         PromptItem(PUB + timedelta(hours=1), ["ZQXC"], "ZQXC names new CEO", None)]


def _claim(symbol="ZQXA", **over):
    return {"symbol": symbol, "claim_type": "price", "claim_text": None, "direction": "up",
            "probability": 65, "expected_move_pct": 2.5, "horizon_days": 5,
            "rationale": "beat", **over}


def _obj(i, *, claims=None, eff="2026-10-16", **over):
    claims = [_claim()] if claims is None else claims
    return {"i": i, "no_claim": not claims, "feedback": "ok", "effective_date": eff,
            "claims": claims, **over}


def _one(obj, items=ITEMS[:1]):
    return parse_response(json.dumps([obj]), items)[1]


# ---- pure: prompt and parsing ------------------------------------------------

def test_e1_build_prompt_exact():
    now_et = datetime(2026, 10, 16, 15, 0, tzinfo=ET)
    assert build_prompt(ITEMS, now_et) == (
        "Now (ET): 2026-10-16 15:00. Items:\n"
        "[1] published 2026-10-16 08:05 ET | symbols: ZQXA,ZQXB | "
        "headline: ZQXA beats estimates | summary: Revenue up 20%.\n"
        "[2] published 2026-10-16 09:05 ET | symbols: ZQXC | "
        "headline: ZQXC names new CEO | summary: -\n"
        'Return: [{"i":1,"no_claim":false,"feedback":"<=200 chars",'
        '"effective_date":"YYYY-MM-DD","claims":[{"symbol":"AAA","claim_type":"price",'
        '"claim_text":null,"direction":"up","probability":60,"expected_move_pct":2.5,'
        '"horizon_days":5,"rationale":"<=160 chars"}]}]')


def test_e2_code_fence_is_stripped():
    body = "```json\n" + json.dumps([_obj(1), _obj(2, claims=[_claim("ZQXC")])]) + "\n```"
    out = parse_response(body, ITEMS)
    assert isinstance(out[1], ItemOut) and isinstance(out[2], ItemOut)
    assert out[2].claims[0].symbol == "ZQXC"


@pytest.mark.parametrize("body", ['{"i": 1}', "not json", "", None])
def test_e3_not_a_list_is_unparsable(body):
    assert parse_response(body, ITEMS) == {1: "unparsable", 2: "unparsable"}


def test_e4_missing_index():
    out = parse_response(json.dumps([_obj(1)]), ITEMS)
    assert isinstance(out[1], ItemOut) and out[2] == "missing"


def test_e5_symbol_outside_item_is_invalid():
    assert _one(_obj(1, claims=[_claim("ZQXC")])).startswith("invalid")


@pytest.mark.parametrize("value, ok", [(True, False), (70.0, True), (70.5, False),
                                       ("70", False), (101, False)])
def test_e6_probability(value, ok):
    out = _one(_obj(1, claims=[_claim(probability=value)]))
    if ok:
        assert out.claims[0].probability == 70 and type(out.claims[0].probability) is int
    else:
        assert out.startswith("invalid")


@pytest.mark.parametrize("no_claim, claims", [(True, [_claim()]), (False, [])])
def test_e7_no_claim_must_match_claims(no_claim, claims):
    assert _one(_obj(1, claims=claims, no_claim=no_claim)).startswith("invalid")


@pytest.mark.parametrize("claim", [
    _claim(claim_text="beats"),
    _claim(claim_type="non_price"),
    _claim(claim_type="non_price", claim_text=""),
    _claim(claim_type="non_price", claim_text="x" * 201),
])
def test_e8_claim_text_rules(claim):
    assert _one(_obj(1, claims=[claim])).startswith("invalid")


def test_e8_non_price_with_text_is_valid():
    out = _one(_obj(1, claims=[_claim(claim_type="non_price", claim_text="Deal closes by Q1")]))
    assert out.claims[0].claim_text == "Deal closes by Q1"


@pytest.mark.parametrize("eff, ok", [("2026-10-14", False), ("2026-10-15", True),
                                     ("2027-02-13", True), ("2027-02-14", False)])
def test_e9_effective_date_window(eff, ok):
    out = _one(_obj(1, eff=eff))
    assert isinstance(out, ItemOut) if ok else out == "invalid: effective_date out of range"


def test_e10_duplicate_symbol_and_duplicate_index():
    assert _one(_obj(1, claims=[_claim(), _claim("zqxa")])).startswith("invalid: duplicate symbol")
    out = parse_response(json.dumps([_obj(1), _obj(1), _obj(2, claims=[_claim("ZQXC")])]), ITEMS)
    assert out[1] == "invalid: duplicate i" and isinstance(out[2], ItemOut)


def test_e11_hit_definition_matches_rules():
    assert "max(1, expected_move_pct/2)" in EXTRACT_SYSTEM
    assert settings.NEWS_HIT_MIN_EXCESS_PCT == 1.0
    assert hit_min_excess(4.0) == 2.0


def test_parse_rounds_and_truncates():
    out = _one(_obj(1, feedback="f" * 500,
                    claims=[_claim(expected_move_pct=2.345, rationale="r" * 400)]))
    assert out.claims[0].expected_move_pct == 2.35
    assert len(out.feedback) == 400 and len(out.claims[0].rationale) == 300


# ---- DB: run_extraction ------------------------------------------------------

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


def _count_items(prompt: str) -> int:
    return sum(1 for line in prompt.splitlines() if line.startswith("["))


def _valid_for(prompt: str, **claim_over) -> str:
    """A valid answer for every item in the prompt: one price claim on its first symbol."""
    out = []
    for line in prompt.splitlines():
        if not line.startswith("["):
            continue
        i = int(line[1:line.index("]")])
        published = line.split("published ", 1)[1][:10]
        first = line.split("symbols: ", 1)[1].split(" |", 1)[0].split(",")[0]
        out.append(_obj(i, eff=published, claims=[_claim(first, **claim_over)]))
    return json.dumps(out)


@pytest_asyncio.fixture
async def db(pg_session_rollback, monkeypatch):
    s = pg_session_rollback
    await s.execute(text("UPDATE news_items SET status='skipped', skip_reason='test_isolation' "
                         "WHERE status='pending'"))
    for key, value in (("NEWS_ENABLED", True), ("AI_PROVIDER", "claude_cli"),
                       ("NEWS_LLM_DAILY_BUDGET", 150), ("NEWS_JUDGE_DAILY_BUDGET", 20),
                       ("CLAUDE_CLI_DAILY_CAP", 300), ("CLAUDE_CLI_TIMEOUT_SECONDS", 60.0),
                       ("NEWS_MAX_CALLS_PER_TICK", 4), ("NEWS_BATCH_SIZE", 8),
                       ("NEWS_MAX_ATTEMPTS", 2), ("NEWS_EXTRACT_MAX_AGE_HOURS", 36)):
        monkeypatch.setattr(settings, key, value)
    source = NewsSource(id=uuid4(), kind="feed", name=f"ZZTEST feed {uuid4().hex[:8]}")
    s.add(source)
    await s.flush()
    s.info["source"] = source
    return s


async def _item(db, *, age=timedelta(hours=1), symbols=(("ZQXA", "shortlist"),
                                                        ("ZQXB", "pool")), attempts=0,
                priority=1):
    now = datetime.now(timezone.utc)
    item = NewsItem(id=uuid4(), origin="live", headline=f"ZZTEST {uuid4().hex}",
                    headline_norm=f"zztest {uuid4().hex}", published_at=now - age,
                    status="pending", priority=priority, extract_attempts=attempts)
    db.add(item)
    await db.flush()
    db.add(NewsItemSource(id=uuid4(), news_id=item.id, source_id=db.info["source"].id,
                          provider="alpaca", external_id=f"zztest-{uuid4().hex}",
                          url="https://example.com/n", published_at=item.published_at))
    for symbol, relevance in symbols:
        db.add(NewsItemSymbol(id=uuid4(), news_id=item.id, symbol=symbol, relevance=relevance))
    await db.flush()
    return item


def _install(monkeypatch, respond) -> FakeLLM:
    fake = FakeLLM(respond)
    monkeypatch.setattr(extract, "call_llm", fake)
    return fake


async def _run(db, **kw):
    return await extract.run_extraction(db, now=datetime.now(timezone.utc),
                                        clock=lambda: CLOCK, **kw)


async def _reload(db, item) -> NewsItem:
    await db.refresh(item)
    return item


async def _symbol_row(db, item, symbol) -> NewsItemSymbol:
    return (await db.execute(select(NewsItemSymbol).where(
        NewsItemSymbol.news_id == item.id, NewsItemSymbol.symbol == symbol))).scalar_one()


async def _seed_calls(db, n, *, context, platform="claude-cli"):
    await db.execute(insert(LLMCall), [
        {"id": uuid4(), "context": context, "platform": platform, "model": "haiku",
         "billing": "subscription"} for _ in range(n)])


@pytest.mark.asyncio
async def test_e12_full_batch_is_written(db, monkeypatch):
    items = [await _item(db, priority=1) for _ in range(8)]
    fake = _install(monkeypatch, _valid_for)
    out = await _run(db)
    assert (out["calls"], out["extracted"], out["failed"], out["llm_stop"]) == (1, 8, 0, None)
    anchor = next_open_session(CLOCK)
    assert anchor == date(2026, 10, 19)
    for item in items:
        item = await _reload(db, item)
        assert (item.status, item.llm_call_id, item.extracted_at) == (
            "extracted", fake.call_ids[0], CLOCK)
        assert item.effective_date == item.published_at.astimezone(ET).date()
        assert item.llm_feedback == "ok"
        claimed = await _symbol_row(db, item, "ZQXA")
        assert (claimed.claim_type, claimed.direction, claimed.probability,
                float(claimed.expected_move_pct), claimed.horizon_days) == (
            "price", "up", 65, 2.5, 5)
        assert (claimed.anchor_session, claimed.evaluate_after) == (anchor, date(2026, 10, 23))
        assert claimed.suggested_action == "review" and claimed.review_status == "open"
        assert claimed.review_at == datetime(2026, 10, 19, 13, 30, tzinfo=timezone.utc)
        assert "source unverified" in claimed.action_reason
        other = await _symbol_row(db, item, "ZQXB")
        assert all(getattr(other, c) is None for c in (
            "claim_type", "direction", "probability", "expected_move_pct", "horizon_days",
            "anchor_session", "evaluate_after", "suggested_action", "review_at"))
        assert other.review_status == "none"
    assert "[8]" in fake.prompts[0] and "[9]" not in fake.prompts[0]


@pytest.mark.asyncio
async def test_e13_only_invalid_items_lose_an_attempt(db, monkeypatch):
    items = [await _item(db, age=timedelta(hours=h)) for h in (1, 2, 3)]
    monkeypatch.setattr(settings, "NEWS_MAX_CALLS_PER_TICK", 1)

    def respond(prompt):
        objs = json.loads(_valid_for(prompt))
        objs[1]["claims"][0]["symbol"] = "ZQXZ"
        return json.dumps(objs)

    _install(monkeypatch, respond)
    out = await _run(db)
    assert (out["extracted"], out["failed"]) == (2, 0)
    got = [(i.status, i.extract_attempts) for i in [await _reload(db, x) for x in items]]
    assert got == [("extracted", 0), ("pending", 1), ("extracted", 0)]


@pytest.mark.asyncio
async def test_e14_unparsable_batch_then_single_retry_then_failed(db, monkeypatch):
    first = await _item(db, age=timedelta(hours=1))
    second = await _item(db, age=timedelta(hours=2))
    monkeypatch.setattr(settings, "NEWS_MAX_CALLS_PER_TICK", 1)
    fake = _install(monkeypatch, lambda prompt: "Sorry, I cannot help with that.")

    await _run(db)
    assert [(i.status, i.extract_attempts) for i in
            (await _reload(db, first), await _reload(db, second))] == [("pending", 1)] * 2
    assert _count_items(fake.prompts[0]) == 2

    out = await _run(db)
    assert "[1]" in fake.prompts[1] and "[2]" not in fake.prompts[1]
    assert first.headline in fake.prompts[1]
    assert out["failed"] == 1
    assert ((await _reload(db, first)).status, (await _reload(db, second)).status) == (
        "failed", "pending")


@pytest.mark.asyncio
async def test_e15_other_provider_makes_no_call(db, monkeypatch):
    await _item(db)
    monkeypatch.setattr(settings, "AI_PROVIDER", "deepseek")
    fake = _install(monkeypatch, _valid_for)
    out = await _run(db)
    assert out["llm"] == "skipped: provider not claude_cli" and fake.prompts == []


@pytest.mark.asyncio
async def test_e16a_news_budget_reached_makes_no_call(db, monkeypatch):
    await _item(db)
    await _seed_calls(db, settings.NEWS_LLM_DAILY_BUDGET, context="news_extraction")
    fake = _install(monkeypatch, _valid_for)
    out = await _run(db)
    assert fake.prompts == [] and out["llm_stop"] == "news budget reached"


@pytest.mark.asyncio
async def test_e16b_other_context_does_not_count_against_news_budget(db, monkeypatch):
    item = await _item(db)
    await _seed_calls(db, 200, context="recommendation_explanation")
    fake = _install(monkeypatch, _valid_for)
    out = await _run(db)
    assert len(fake.prompts) == 1 and out["extracted"] == 1
    assert (await _reload(db, item)).status == "extracted"


@pytest.mark.asyncio
async def test_e17_zero_budget_makes_no_call(db, monkeypatch):
    await _item(db)
    monkeypatch.setattr(settings, "NEWS_LLM_DAILY_BUDGET", 0)
    fake = _install(monkeypatch, _valid_for)
    out = await _run(db)
    assert fake.prompts == [] and out["llm_stop"] == "news budget reached"


@pytest.mark.asyncio
async def test_e18_budget_over_cap_reserve_is_skipped(db, monkeypatch):
    await _item(db)
    monkeypatch.setattr(settings, "NEWS_LLM_DAILY_BUDGET", 260)
    fake = _install(monkeypatch, _valid_for)
    out = await _run(db)
    assert out["llm"] == "skipped: news budget exceeds cap reserve" and fake.prompts == []


@pytest.mark.asyncio
async def test_e19_skipped_call_stops_the_tick(db, monkeypatch):
    items = [await _item(db, age=timedelta(hours=h)) for h in (1, 2)]
    monkeypatch.setattr(settings, "NEWS_BATCH_SIZE", 1)
    fake = _install(monkeypatch, lambda prompt: ("skip", "claude-cli daily cap reached"))
    out = await _run(db)
    assert len(fake.prompts) == 1 and out["calls"] == 0
    assert out["llm_stop"] == "claude-cli daily cap reached"
    assert [(await _reload(db, i)).extract_attempts for i in items] == [0, 0]


@pytest.mark.asyncio
async def test_e20_failed_call_costs_an_attempt_and_stops_the_tick(db, monkeypatch):
    items = [await _item(db, age=timedelta(hours=h)) for h in (1, 2)]
    monkeypatch.setattr(settings, "NEWS_BATCH_SIZE", 1)
    fake = _install(monkeypatch, lambda prompt: ("fail", "timeout after 60s"))
    out = await _run(db)
    assert len(fake.prompts) == 1 and out["llm_stop"] == "timeout after 60s"
    assert [(await _reload(db, i)).extract_attempts for i in items] == [1, 0]


@pytest.mark.asyncio
async def test_e21_stale_items_are_skipped_before_the_provider_check(db, monkeypatch):
    old = await _item(db, age=timedelta(hours=37))
    fresh = await _item(db, age=timedelta(hours=35))
    monkeypatch.setattr(settings, "AI_PROVIDER", "deepseek")
    out = await _run(db)
    assert out["stale"] == 1
    old, fresh = await _reload(db, old), await _reload(db, fresh)
    assert (old.status, old.skip_reason) == ("skipped", "stale")
    assert (fresh.status, fresh.skip_reason) == ("pending", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout, calls", [(60.0, 4), (120.0, 3)])
async def test_e22_calls_per_tick(db, monkeypatch, timeout, calls):
    for h in range(40):
        await _item(db, age=timedelta(minutes=10 + h))
    monkeypatch.setattr(settings, "CLAUDE_CLI_TIMEOUT_SECONDS", timeout)
    fake = _install(monkeypatch, _valid_for)
    out = await _run(db)
    assert len(fake.prompts) == calls and out["extracted"] == 8 * calls


@pytest.mark.asyncio
async def test_e23_disabled(db, monkeypatch):
    await _item(db)
    monkeypatch.setattr(settings, "NEWS_ENABLED", False)
    fake = _install(monkeypatch, _valid_for)
    assert await _run(db) == {"llm": "disabled"} and fake.prompts == []


_FAKE_CLAUDE = '''#!{python}
import json, sys
sys.stdin.read()
print(json.dumps({{"type": "result", "is_error": False, "result": {answer!r},
                  "total_cost_usd": 0.001,
                  "usage": {{"input_tokens": 2, "cache_creation_input_tokens": 600,
                            "output_tokens": 90}},
                  "modelUsage": {{}}}}))
'''


@pytest.mark.asyncio
async def test_e24_real_call_llm_with_fake_cli(db, monkeypatch, tmp_path):
    item = await _item(db)
    eff = item.published_at.astimezone(ET).date().isoformat()
    answer = json.dumps([_obj(1, eff=eff)])
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

    out = await _run(db)
    assert (out["calls"], out["extracted"], out["llm_stop"]) == (1, 1, None)
    item = await _reload(db, item)
    assert item.status == "extracted" and item.llm_call_id is not None
    row = (await db.execute(select(LLMCall).where(LLMCall.id == item.llm_call_id))).scalar_one()
    assert (row.context, row.platform, row.billing, row.success) == (
        "news_extraction", "claude-cli", "subscription", True)
    assert row.input_tokens + row.cache_read_tokens + row.cache_creation_tokens == 602


def test_build_prompt_flattens_whitespace_and_entities():
    item = PromptItem(PUB, ["ZQXA"], "ZQXA &amp; ZQXB:\n  CEO&#39;s\tplan",
                      "Line one.\n\nLine two &#39;x&#39;")
    line = build_prompt([item], datetime(2026, 10, 16, 15, 0, tzinfo=ET)).splitlines()[1]
    assert line.endswith("headline: ZQXA & ZQXB: CEO's plan | summary: Line one. Line two 'x'")


async def _news_calls_today(db) -> int:
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (await db.execute(select(func.count()).select_from(LLMCall).where(
        LLMCall.context == "news_extraction", LLMCall.created_at >= day))).scalar()


@pytest.mark.asyncio
async def test_nul_bytes_are_stripped_before_storing(db, monkeypatch):
    item = await _item(db)

    def respond(prompt):
        objs = json.loads(_valid_for(prompt, claim_type="non_price", claim_text="Deal\x00 closes",
                                     rationale="r\x00"))
        objs[0]["feedback"] = "fee\x00dback"
        return json.dumps(objs)

    _install(monkeypatch, respond)
    out = await _run(db)
    assert out["extracted"] == 1
    item = await _reload(db, item)
    assert (item.status, item.llm_feedback) == ("extracted", "feedback")
    row = await _symbol_row(db, item, "ZQXA")
    assert (row.claim_text, row.rationale) == ("Deal closes", "r")


async def _assert_only_bad_item_lost(db, fake, items, bad, calls_before):
    assert await _news_calls_today(db) == calls_before + 1
    assert (await db.execute(select(LLMCall.id).where(
        LLMCall.id == fake.call_ids[0]))).scalar_one() == fake.call_ids[0]
    for item in items:
        item = await _reload(db, item)
        if item.id == bad.id:
            assert (item.status, item.extract_attempts, item.llm_call_id) == ("pending", 1, None)
            row = await _symbol_row(db, item, "ZQXA")
            await db.refresh(row)
            assert (row.claim_type, row.suggested_action, row.review_status) == (
                None, None, "none")
        else:
            assert (item.status, item.extract_attempts) == ("extracted", 0)


@pytest.mark.asyncio
async def test_store_failure_costs_only_that_item(db, monkeypatch):
    items = [await _item(db, age=timedelta(hours=h)) for h in (1, 2, 3)]
    bad = items[1]
    real_write = extract._write_claim

    def write_then_fail(row, claim, stats, *, at, anchor):
        real_write(row, claim, stats, at=at, anchor=anchor)
        if row.news_id == bad.id:
            raise IndexError("advance 5 sessions out of calendar range")

    monkeypatch.setattr(extract, "_write_claim", write_then_fail)
    monkeypatch.setattr(settings, "NEWS_MAX_CALLS_PER_TICK", 1)
    fake = _install(monkeypatch, _valid_for)
    calls_before = await _news_calls_today(db)
    out = await _run(db)
    assert (out["calls"], out["extracted"], out["failed"]) == (1, 2, 0)
    await _assert_only_bad_item_lost(db, fake, items, bad, calls_before)


@pytest.mark.asyncio
async def test_database_rejected_item_costs_only_itself(db, monkeypatch):
    items = [await _item(db, age=timedelta(hours=h)) for h in (1, 2)]
    bad = items[0]

    def respond(prompt):
        objs = json.loads(_valid_for(prompt))
        objs[0]["feedback"] = "rejected\x00"
        return json.dumps(objs)

    monkeypatch.setattr(extract, "_clean", lambda value: value)
    monkeypatch.setattr(settings, "NEWS_MAX_CALLS_PER_TICK", 1)
    fake = _install(monkeypatch, respond)
    calls_before = await _news_calls_today(db)
    out = await _run(db)
    assert (out["calls"], out["extracted"]) == (1, 1)
    await _assert_only_bad_item_lost(db, fake, items, bad, calls_before)


@pytest.mark.asyncio
async def test_call_row_is_committed_before_the_batch_is_stored(db, monkeypatch):
    item = await _item(db)
    await db.commit()

    async def broken(_db, _news_ids):
        raise RuntimeError("boom")

    monkeypatch.setattr(extract, "_source_stats", broken)
    fake = _install(monkeypatch, _valid_for)
    with pytest.raises(RuntimeError):
        await _run(db)
    await db.rollback()
    assert (await db.execute(select(LLMCall.id).where(
        LLMCall.id == fake.call_ids[0]))).scalar_one() == fake.call_ids[0]
    item = await _reload(db, item)
    assert (item.status, item.extract_attempts) == ("pending", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind, action", [("platform", "review"), ("feed", "buy")])
async def test_platform_aggregate_never_verifies_a_claim(db, monkeypatch, kind, action):
    for key, value in (("NEWS_SOURCE_MIN_N", 20), ("NEWS_ACTION_MIN_LIFT", 1.2),
                       ("NEWS_ACTION_MIN_PROB", 70), ("NEWS_REVIEW_MIN_PROB", 60)):
        monkeypatch.setattr(settings, key, value)
    source = NewsSource(id=uuid4(), kind=kind, name=f"ZZTEST {kind} {uuid4().hex[:8]}",
                        n_evaluated=25, hits=20, lift=1.5)
    db.add(source)
    await db.flush()
    db.info["source"] = source
    item = await _item(db)
    _install(monkeypatch, lambda prompt: _valid_for(prompt, probability=85))
    await _run(db)
    claimed = await _symbol_row(db, item, "ZQXA")
    assert claimed.suggested_action == action
    if kind == "platform":
        assert "source unverified" in claimed.action_reason
