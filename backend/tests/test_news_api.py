"""P6c news API against real PostgreSQL (rolled back): the feed and its keyset
cursor, filters and 422s, the item detail with its shared LLM call, the review
queue and its admin-only close (409 once closed), the sources list and the
admin-only toggle (reason required, platforms refused), and a guard that the
router never reaches the LLM, extraction, notifications or a task."""

import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text

import app.models_registry  # noqa: F401
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.modules.auth.models import User, UserRole
from app.modules.llm import client as llm_client
from app.modules.llm.models import LLMCall
from app.modules.news import extract, notify
from app.modules.news import router as news_router
from app.modules.news.models import (
    PLATFORM_BENZINGA, NewsItem, NewsItemSource, NewsItemSymbol, NewsSource
)
from app.modules.news.router import router
from app.modules.simledger.models import HeartbeatRecord
from app.tasks import quant_tasks
from tests._pg_rollback import pg_session_rollback  # noqa: F401

URL = "/api/v1/news"
T0 = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)

ITEM_KEYS = {"id", "headline", "published_at", "discovered_at", "origin", "status",
             "skip_reason", "effective_date", "extracted_at", "sources", "symbols"}
SYMBOL_KEYS = {"id", "symbol", "relevance", "claim_type", "direction", "probability",
               "expected_move_pct", "horizon_days", "suggested_action", "review_status"}
SYMBOL_FULL_KEYS = SYMBOL_KEYS | {"claim_text", "rationale", "anchor_session",
                                  "evaluate_after", "action_reason", "review_at",
                                  "reviewed_at", "review_note"}
QUEUE_KEYS = SYMBOL_FULL_KEYS | {"news_id", "headline", "published_at"}
SOURCE_KEYS = {"id", "kind", "name", "parent", "enabled", "disabled_at", "disabled_by",
               "disabled_reason", "score", "lift", "n_evaluated", "hits",
               "auto_disable_after_n", "score_updated_at"}


@pytest_asyncio.fixture
async def api(pg_session_rollback):
    db = pg_session_rollback
    admin_id = (await db.execute(select(User.id).where(
        User.role == UserRole.SUPER_ADMIN).limit(1))).scalar_one()
    user = SimpleNamespace(id=admin_id, role=UserRole.SUPER_ADMIN, is_active=True)
    platform = (await db.execute(select(NewsSource).where(
        NewsSource.kind == "platform", NewsSource.name == PLATFORM_BENZINGA))
    ).scalar_one_or_none()
    if platform is None:
        platform = NewsSource(id=uuid4(), kind="platform", name=PLATFORM_BENZINGA)
        db.add(platform)
        await db.commit()

    async def _db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        yield SimpleNamespace(client=client, db=db, user=user, platform_id=platform.id)


async def _source(api, *, kind="author", parent=True, **over):
    """A committed source; a failed request rolls the session back, so tests
    keep only ids across requests."""
    src = NewsSource(id=uuid4(), kind=kind, name=f"ZQXP6C {kind} {uuid4().hex[:8]}",
                     parent_id=api.platform_id if parent and kind == "author" else None,
                     **over)
    api.db.add(src)
    await api.db.commit()
    return src.id


def _claim(action="buy", *, review=None, claim_type="price", **over):
    review_status, review_at = review or ("none", None)
    row = {"relevance": "held", "claim_type": claim_type, "direction": "up",
           "probability": 72, "expected_move_pct": 3.5, "horizon_days": 5,
           "anchor_session": date(2026, 10, 19), "evaluate_after": date(2026, 10, 23),
           "suggested_action": action, "action_reason": "p=72, held, source unverified",
           "rationale": "beat", "review_status": review_status, "review_at": review_at,
           "claim_text": "Deal closes by Q1" if claim_type == "non_price" else None}
    row.update(over)
    return row


async def _item(api, *, published_at=T0, status="extracted", origin="live",
                symbols=(("ZQXP6CA", None),), source_ids=(), llm_call_id=None,
                item_id=None):
    """A committed story. ``symbols`` is ((symbol, claim-dict-or-None), ...)."""
    extracted = status in ("extracted", "no_claim")
    item = NewsItem(id=item_id or uuid4(), origin=origin,
                    headline=f"ZQXP6C {uuid4().hex}", headline_norm=f"zqxp6c {uuid4().hex}",
                    summary="ZQXP6C summary", published_at=published_at, status=status,
                    skip_reason="stale" if status in ("skipped", "irrelevant") else None,
                    priority=1, extracted_at=published_at + timedelta(minutes=5) if extracted
                    else None, effective_date=date(2026, 10, 16) if extracted else None,
                    llm_feedback="ok" if extracted else None, llm_call_id=llm_call_id)
    api.db.add(item)
    await api.db.flush()
    for n, sid in enumerate(source_ids):
        api.db.add(NewsItemSource(id=uuid4(), news_id=item.id, source_id=sid,
                                  provider="alpaca", external_id=f"zqxp6c-{uuid4().hex}",
                                  url=f"https://example.com/{n}", published_at=published_at))
    symbol_ids = {}
    for symbol, claim in symbols:
        sid = uuid4()
        fields = claim if claim is not None else {"relevance": "pool"}
        api.db.add(NewsItemSymbol(id=sid, news_id=item.id, symbol=symbol, **fields))
        symbol_ids[symbol] = sid
    await api.db.commit()
    return SimpleNamespace(id=item.id, symbols=symbol_ids)


async def _call(api, **over):
    call = LLMCall(id=uuid4(), context="news_extraction", platform="claude-cli", model="haiku",
                   billing="subscription", **over)
    api.db.add(call)
    await api.db.commit()
    return call.id


async def _fresh(api, model, key):
    return (await api.db.execute(select(model).where(model.id == key)
                                 .execution_options(populate_existing=True))).scalar_one()


async def _feed(api, **params):
    r = await api.client.get(f"{URL}/items", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---- A: feed -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a1_feed_shape_order_and_meta(api):
    src = await _source(api)
    old = await _item(api, published_at=T0, source_ids=[src])
    new = await _item(api, published_at=T0 + timedelta(hours=1), source_ids=[src],
                      symbols=(("ZQXP6CA", _claim()), ("ZQXP6CB", None)))
    body = await _feed(api, symbol=" zqxp6ca ")
    assert set(body) == {"items", "next_cursor", "meta"}
    assert [i["id"] for i in body["items"]] == [str(new.id), str(old.id)]
    assert body["next_cursor"] is None
    first = body["items"][0]
    assert set(first) == ITEM_KEYS
    assert [s["symbol"] for s in first["symbols"]] == ["ZQXP6CA", "ZQXP6CB"]
    assert all(set(s) == SYMBOL_KEYS for s in first["symbols"])
    assert first["symbols"][0]["expected_move_pct"] == 3.5
    assert first["symbols"][1]["claim_type"] is None
    assert first["sources"] == [{"name": first["sources"][0]["name"], "kind": "author",
                                 "url": "https://example.com/0"}]
    assert set(body["meta"]) == {"budget_used_today", "budget", "pending", "last_cycle"}


@pytest.mark.asyncio
async def test_a2_cursor_pages_without_gaps_or_repeats(api):
    stamps = [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=1), T0 + timedelta(hours=1),
              T0 + timedelta(hours=2)]
    items = [await _item(api, published_at=at) for at in stamps]
    expected = [str(i.id) for _, i in sorted(zip(stamps, items),
                                             key=lambda p: (p[0], p[1].id), reverse=True)]
    seen, cursor, pages = [], None, []
    while True:
        params = {"symbol": "ZQXP6CA", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = await _feed(api, **params)
        pages.append(len(body["items"]))
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == [2, 2, 1]
    assert seen == expected


@pytest.mark.parametrize("params, picks", [
    ({"status": "pending"}, ["pending"]),
    ({"action": "buy"}, ["buy"]),
    ({"origin": "backfill"}, ["pending"]),
    ({"action": "review", "origin": "live"}, ["review"]),
])
@pytest.mark.asyncio
async def test_a3_filters(api, params, picks):
    made = {
        "buy": await _item(api, published_at=T0, symbols=(("ZQXP6CA", _claim("buy")),)),
        "pending": await _item(api, published_at=T0 + timedelta(minutes=1), status="pending",
                               origin="backfill"),
        "review": await _item(api, published_at=T0 + timedelta(minutes=2),
                              symbols=(("ZQXP6CA", _claim("review")),)),
    }
    body = await _feed(api, symbol="ZQXP6CA", **params)
    assert [i["id"] for i in body["items"]] == [str(made[k].id) for k in picks]


@pytest.mark.parametrize("params", [
    {"status": "bogus"}, {"action": "hold"}, {"origin": "rss"}, {"limit": 0}, {"limit": 201},
    {"symbol": "Z" * 21}, {"cursor": "nope"},
    {"cursor": f"2026-10-16T12:00:00|{uuid4()}"},
    {"cursor": "2026-10-16T12:00:00+00:00|not-a-uuid"},
])
@pytest.mark.asyncio
async def test_a4_bad_params_are_422(api, params):
    r = await api.client.get(f"{URL}/items", params=params)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_a5_meta_counts_only_todays_news_calls(api):
    before = (await _feed(api, symbol="ZQXP6CA"))["meta"]
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for context, at in (("news_extraction", None), ("news_extraction", None),
                        ("recommendation_explanation", None),
                        ("news_extraction", day_start - timedelta(minutes=1))):
        api.db.add(LLMCall(id=uuid4(), context=context, platform="claude-cli", model="haiku",
                           billing="subscription",
                           **({"created_at": at} if at else {})))
    await _item(api, status="pending")
    beat = (await api.db.execute(select(HeartbeatRecord).where(
        HeartbeatRecord.name == "news_cycle"))).scalar_one_or_none()
    meta = {"extract": {"llm": "skipped: provider not claude_cli"}, "zqxp6c": 1}
    if beat is None:
        api.db.add(HeartbeatRecord(name="news_cycle", meta=meta))
    else:
        beat.meta = meta
    await api.db.commit()
    after = (await _feed(api, symbol="ZQXP6CA"))["meta"]
    assert after["budget_used_today"] - before["budget_used_today"] == 2
    assert after["pending"] - before["pending"] == 1
    assert after["budget"] == settings.NEWS_LLM_DAILY_BUDGET
    assert after["last_cycle"]["meta"] == meta and after["last_cycle"]["at"]


# ---- B: item detail ----------------------------------------------------------

@pytest.mark.asyncio
async def test_b1_detail_with_shared_llm_call(api):
    call_id = await _call(api, input_tokens=2, cache_read_tokens=100,
                          cache_creation_tokens=600, output_tokens=90, cost_usd=0.001,
                          latency_ms=4200)
    src = await _source(api)
    item = await _item(api, llm_call_id=call_id, source_ids=[src],
                       symbols=(("ZQXP6CA", _claim(claim_type="non_price")),))
    for _ in range(2):
        await _item(api, llm_call_id=call_id)
    r = await api.client.get(f"{URL}/items/{item.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == ITEM_KEYS | {"summary", "llm_feedback", "priority",
                                     "extract_attempts", "updated_at", "llm"}
    assert set(body["symbols"][0]) == SYMBOL_FULL_KEYS
    assert body["symbols"][0]["claim_text"] == "Deal closes by Q1"
    assert body["symbols"][0]["anchor_session"] == "2026-10-19"
    link = body["sources"][0]
    assert set(link) == {"id", "provider", "external_id", "url", "published_at",
                         "fetched_at", "source"}
    assert link["source"]["id"] == str(src) and link["source"]["lift"] is None
    assert set(link["source"]) == {"id", "name", "kind", "enabled", "score", "lift",
                                   "n_evaluated"}
    llm = body["llm"]
    assert (llm["batch_size"], llm["total_input_tokens"], llm["input_tokens"],
            llm["output_tokens"], llm["cost_usd"]) == (3, 702, 2, 90, 0.001)
    assert (llm["platform"], llm["billing"], llm["model"]) == ("claude-cli", "subscription",
                                                               "haiku")


@pytest.mark.parametrize("item_id, code", [(uuid4(), 404), ("not-a-uuid", 422)])
@pytest.mark.asyncio
async def test_b2_missing_or_malformed_item(api, item_id, code):
    r = await api.client.get(f"{URL}/items/{item_id}")
    assert r.status_code == code, r.text


@pytest.mark.asyncio
async def test_b3_item_without_llm_call(api):
    item = await _item(api, status="pending")
    r = await api.client.get(f"{URL}/items/{item.id}")
    assert r.status_code == 200 and r.json()["llm"] is None


# ---- C: review queue -----------------------------------------------------------

async def _review_rows(api):
    now = datetime.now(timezone.utc)
    item = await _item(api, symbols=(
        ("ZQXP6CA", _claim("review", review=("open", now - timedelta(hours=1)))),
        ("ZQXP6CB", _claim("review", review=("open", now + timedelta(days=1)),
                           relevance="shortlist")),
        ("ZQXP6CC", _claim("review", review=("done", now - timedelta(hours=2)),
                           reviewed_at=now)),
        ("ZQXP6CD", _claim("ignore")),
    ))
    return item


def _ours(rows, item):
    return [r["symbol"] for r in rows if r["news_id"] == str(item.id)]


@pytest.mark.asyncio
async def test_c1_queue_due_vs_upcoming(api):
    item = await _review_rows(api)
    due = await api.client.get(f"{URL}/review-queue")
    assert due.status_code == 200
    assert _ours(due.json(), item) == ["ZQXP6CA"]
    row = next(r for r in due.json() if r["news_id"] == str(item.id))
    assert set(row) == QUEUE_KEYS and row["headline"].startswith("ZQXP6C")
    upcoming = await api.client.get(f"{URL}/review-queue",
                                    params={"include_upcoming": "true"})
    assert _ours(upcoming.json(), item) == ["ZQXP6CA", "ZQXP6CB"]


@pytest.mark.asyncio
async def test_c2_done_then_conflict(api):
    item = await _review_rows(api)
    sid = item.symbols["ZQXP6CA"]
    before = await _fresh(api, NewsItemSymbol, sid)
    review_at = before.review_at
    r = await api.client.patch(f"{URL}/symbols/{sid}/review",
                               json={"status": "done", "note": "  checked  "})
    assert r.status_code == 200, r.text
    assert set(r.json()) == QUEUE_KEYS and r.json()["review_status"] == "done"
    row = await _fresh(api, NewsItemSymbol, sid)
    assert (row.review_status, row.review_note, row.reviewed_by) == ("done", "checked",
                                                                     api.user.id)
    assert row.reviewed_at is not None and row.review_at == review_at
    again = await api.client.patch(f"{URL}/symbols/{sid}/review", json={"status": "dismissed"})
    assert again.status_code == 409 and again.json()["detail"] == "review is done, not open"
    assert _ours((await api.client.get(f"{URL}/review-queue")).json(), item) == []


@pytest.mark.asyncio
async def test_c2_dismiss_blank_note_and_closed_rows(api):
    item = await _review_rows(api)
    r = await api.client.patch(f"{URL}/symbols/{item.symbols['ZQXP6CB']}/review",
                               json={"status": "dismissed", "note": "   "})
    assert r.status_code == 200
    row = await _fresh(api, NewsItemSymbol, item.symbols["ZQXP6CB"])
    assert (row.review_status, row.review_note) == ("dismissed", None)
    none = await api.client.patch(f"{URL}/symbols/{item.symbols['ZQXP6CD']}/review",
                                  json={"status": "done"})
    assert none.status_code == 409 and none.json()["detail"] == "review is none, not open"


@pytest.mark.asyncio
async def test_c2_unknown_symbol_404(api):
    r = await api.client.patch(f"{URL}/symbols/{uuid4()}/review", json={"status": "done"})
    assert r.status_code == 404 and r.json()["detail"] == "news symbol not found"


@pytest.mark.parametrize("body", [
    {"status": "open"}, {}, {"status": "done", "extra": 1}, {"status": "done", "note": "x" * 501},
])
@pytest.mark.asyncio
async def test_c3_review_body_422(api, body):
    item = await _review_rows(api)
    sid = item.symbols["ZQXP6CA"]
    r = await api.client.patch(f"{URL}/symbols/{sid}/review", json=body)
    assert r.status_code == 422, r.text
    assert (await _fresh(api, NewsItemSymbol, sid)).review_status == "open"


@pytest.mark.parametrize("role", [UserRole.BASIC, UserRole.PREMIUM])
@pytest.mark.asyncio
async def test_c4_non_admin_reads_but_cannot_write(api, role):
    item = await _review_rows(api)
    sid = item.symbols["ZQXP6CA"]
    src = await _source(api)
    api.user.role = role
    for path in ("/items", "/review-queue", "/sources"):
        assert (await api.client.get(URL + path)).status_code == 200
    r1 = await api.client.patch(f"{URL}/symbols/{sid}/review", json={"status": "done"})
    r2 = await api.client.patch(f"{URL}/sources/{src}",
                                json={"enabled": False, "reason": "noisy"})
    assert (r1.status_code, r2.status_code) == (403, 403)
    assert (await _fresh(api, NewsItemSymbol, sid)).review_status == "open"
    assert (await _fresh(api, NewsSource, src)).enabled is True


# ---- D: sources ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_d1_sources_list_and_filter(api):
    src = await _source(api)
    r = await api.client.get(f"{URL}/sources")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"sources", "thresholds"}
    assert body["thresholds"] == {
        "min_n": settings.NEWS_SOURCE_MIN_N,
        "action_min_lift": settings.NEWS_ACTION_MIN_LIFT,
        "disable_below_lift": settings.NEWS_DISABLE_BELOW_LIFT,
        "consecutive_days": settings.NEWS_DISABLE_CONSECUTIVE_DAYS}
    mine = next(s for s in body["sources"] if s["id"] == str(src))
    assert set(mine) == SOURCE_KEYS and mine["parent"] == "benzinga" and mine["lift"] is None
    kinds = [s["kind"] for s in body["sources"]]
    assert kinds == sorted(kinds)
    authors = (await api.client.get(f"{URL}/sources", params={"kind": "author"})).json()
    assert authors["sources"] and {s["kind"] for s in authors["sources"]} == {"author"}
    platforms = (await api.client.get(f"{URL}/sources", params={"kind": "platform"})).json()
    assert [s["name"] for s in platforms["sources"]] == ["benzinga"]
    assert platforms["sources"][0]["parent"] is None
    assert (await api.client.get(f"{URL}/sources", params={"kind": "bogus"})).status_code == 422


@pytest.mark.parametrize("body", [{"enabled": False}, {"enabled": False, "reason": "   "}])
@pytest.mark.asyncio
async def test_d2_disable_needs_reason(api, body):
    src = await _source(api)
    r = await api.client.patch(f"{URL}/sources/{src}", json=body)
    assert r.status_code == 422
    assert r.json()["detail"] == [{"loc": ["body", "reason"],
                                   "msg": "a reason is required to disable a source"}]
    assert (await _fresh(api, NewsSource, src)).enabled is True


@pytest.mark.asyncio
async def test_d2_disable_already_disabled_still_needs_reason(api):
    src = await _source(api, enabled=False, disabled_at=T0, disabled_by="auto",
                        disabled_reason="low lift")
    r = await api.client.patch(f"{URL}/sources/{src}", json={"enabled": False})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_d2_disable_writes_three_fields(api):
    src = await _source(api)
    r = await api.client.patch(f"{URL}/sources/{src}",
                               json={"enabled": False, "reason": "  noisy "})
    assert r.status_code == 200, r.text
    assert set(r.json()) == SOURCE_KEYS and r.json()["parent"] == "benzinga"
    row = await _fresh(api, NewsSource, src)
    assert (row.enabled, row.disabled_by, row.disabled_reason) == (False, "manual", "noisy")
    assert row.disabled_at is not None


@pytest.mark.asyncio
async def test_d3_reenable_resets_and_defers_auto_disable(api):
    src = await _source(api, enabled=False, disabled_at=T0, disabled_by="auto",
                        disabled_reason="low lift", n_evaluated=7)
    r = await api.client.patch(f"{URL}/sources/{src}", json={"enabled": True})
    assert r.status_code == 200, r.text
    row = await _fresh(api, NewsSource, src)
    assert row.enabled is True
    assert (row.disabled_at, row.disabled_by, row.disabled_reason) == (None, None, None)
    assert row.auto_disable_after_n == 7 + settings.NEWS_SOURCE_MIN_N


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.asyncio
async def test_d4_same_state_is_noop(api, enabled):
    over = {} if enabled else {"enabled": False, "disabled_at": T0, "disabled_by": "auto",
                               "disabled_reason": "low lift"}
    src = await _source(api, n_evaluated=3, auto_disable_after_n=40, **over)
    r = await api.client.patch(f"{URL}/sources/{src}",
                               json={"enabled": enabled, "reason": "other"})
    assert r.status_code == 200, r.text
    row = await _fresh(api, NewsSource, src)
    assert (row.enabled, row.auto_disable_after_n) == (enabled, 40)
    assert (row.disabled_at, row.disabled_by, row.disabled_reason) == (
        (None, None, None) if enabled else (T0, "auto", "low lift"))


@pytest.mark.parametrize("body", [{"enabled": False, "reason": "noisy"}, {"enabled": True}])
@pytest.mark.asyncio
async def test_d5_platform_is_refused(api, body):
    before = await _fresh(api, NewsSource, api.platform_id)
    state = (before.enabled, before.disabled_at, before.auto_disable_after_n)
    r = await api.client.patch(f"{URL}/sources/{api.platform_id}", json=body)
    assert r.status_code == 409
    assert r.json()["detail"].startswith("platform sources are not toggled here")
    after = await _fresh(api, NewsSource, api.platform_id)
    assert (after.enabled, after.disabled_at, after.auto_disable_after_n) == state


@pytest.mark.parametrize("target, body, code", [
    ("missing", {"enabled": True}, 404),
    ("author", {"enabled": False, "reason": "noisy", "by": "me"}, 422),
    ("author", {"enabled": False, "reason": "x" * 301}, 422),
])
@pytest.mark.asyncio
async def test_d6_source_patch_errors(api, target, body, code):
    src = uuid4() if target == "missing" else await _source(api)
    r = await api.client.patch(f"{URL}/sources/{src}", json=body)
    assert r.status_code == code, r.text
    if target == "author":
        assert (await _fresh(api, NewsSource, src)).enabled is True


# ---- E: no LLM, extraction, notification or task path -------------------------

ALLOWED_IMPORTS = {
    "__future__", "datetime", "typing", "uuid", "fastapi", "pydantic", "sqlalchemy",
    "sqlalchemy.orm", "app.config", "app.dependencies", "app.modules.llm.models",
    "app.modules.news.models", "app.modules.simledger.models",
}


def test_e1_router_imports_stay_inside_the_allowed_set():
    source = inspect.getsource(news_router)
    modules, sqla = set(), set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
            if node.module == "sqlalchemy":
                sqla |= {a.name for a in node.names}
    assert modules <= ALLOWED_IMPORTS, modules - ALLOWED_IMPORTS
    assert sqla <= {"func", "select", "tuple_", "update"}
    assert "call_llm" not in source


@pytest.mark.asyncio
async def test_e2_no_endpoint_reaches_llm_extraction_or_notify(api, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("must not be called")

    for target, name in ((llm_client, "call_llm"), (extract, "call_llm"),
                         (extract, "run_extraction"), (notify, "maybe_notify"),
                         (quant_tasks, "_notify")):
        monkeypatch.setattr(target, name, _boom)
    item = await _review_rows(api)
    src = await _source(api)
    calls = [
        ("get", f"{URL}/items", None),
        ("get", f"{URL}/items/{item.id}", None),
        ("get", f"{URL}/review-queue", None),
        ("patch", f"{URL}/symbols/{item.symbols['ZQXP6CA']}/review", {"status": "done"}),
        ("get", f"{URL}/sources", None),
        ("patch", f"{URL}/sources/{src}", {"enabled": False, "reason": "noisy"}),
    ]
    for method, url, body in calls:
        r = await getattr(api.client, method)(url, **({"json": body} if body else {}))
        assert r.status_code == 200, (url, r.text)


def test_e3_budget_context_matches_extraction():
    assert news_router.NEWS_CONTEXT == extract.CONTEXT


# ---- fix round 1: race message and count / order / limit pins -------------------

@pytest.mark.asyncio
async def test_c2_conflict_reports_the_current_status_of_a_stale_row(api):
    item = await _review_rows(api)
    sid = item.symbols["ZQXP6CA"]
    stale = await _fresh(api, NewsItemSymbol, sid)
    assert stale.review_status == "open"
    await api.db.execute(text("UPDATE news_item_symbols SET review_status='done', reviewed_at=now() "
                              "WHERE id = :id"), {"id": sid})
    await api.db.commit()
    r = await api.client.patch(f"{URL}/symbols/{sid}/review", json={"status": "dismissed"})
    assert r.status_code == 409 and r.json()["detail"] == "review is done, not open"


@pytest.mark.asyncio
async def test_a5_pending_counts_only_pending_items(api):
    before = (await _feed(api, symbol="ZQXP6CA"))["meta"]["pending"]
    for status in ("pending", "pending", "extracted"):
        await _item(api, status=status)
    after = (await _feed(api, symbol="ZQXP6CA"))["meta"]["pending"]
    assert after - before == 2


@pytest.mark.asyncio
async def test_b1_batch_size_counts_only_items_of_that_call(api):
    first = await _call(api)
    other = await _call(api)
    item = await _item(api, llm_call_id=first)
    for _ in range(2):
        await _item(api, llm_call_id=first)
    await _item(api, llm_call_id=other)
    body = (await api.client.get(f"{URL}/items/{item.id}")).json()
    assert body["llm"]["batch_size"] == 3


@pytest.mark.asyncio
async def test_d1_sources_ordered_by_kind_then_name(api):
    tag = uuid4().hex[:6]
    made = {}
    for kind, name in (("author", "b"), ("feed", "0"), ("author", "c"), ("author", "a")):
        src = NewsSource(id=uuid4(), kind=kind, name=f"ZQXP6C {tag} {name}",
                         parent_id=api.platform_id if kind == "author" else None)
        api.db.add(src)
        made[str(src.id)] = (kind, src.name)
    await api.db.commit()
    body = (await api.client.get(f"{URL}/sources")).json()
    mine = [made[s["id"]] for s in body["sources"] if s["id"] in made]
    assert mine == sorted(made.values())


@pytest.mark.asyncio
async def test_b1_detail_sources_ordered_by_fetched_at_then_id(api):
    src = await _source(api)
    item = await _item(api, status="pending")
    u0, u1, u2, u3 = sorted(uuid4() for _ in range(4))
    hour = timedelta(hours=1)
    for link_id, fetched in ((u0, T0 + 2 * hour), (u3, T0), (u1, T0 + hour), (u2, T0 + hour)):
        api.db.add(NewsItemSource(id=link_id, news_id=item.id, source_id=src, provider="alpaca",
                                  external_id=f"zqxp6c-{uuid4().hex}", published_at=T0,
                                  fetched_at=fetched))
        await api.db.flush()
    await api.db.commit()
    body = (await api.client.get(f"{URL}/items/{item.id}")).json()
    assert [UUID(link["id"]) for link in body["sources"]] == [u3, u1, u2, u0]


@pytest.mark.asyncio
async def test_c1_queue_limit_keeps_the_earliest_due(api):
    early = datetime(2000, 1, 1, tzinfo=timezone.utc)
    item = await _item(api, symbols=tuple(
        (f"ZQXP6C{n}", _claim("review", review=("open", early + timedelta(hours=n))))
        for n in (3, 1, 2)))
    r = await api.client.get(f"{URL}/review-queue", params={"limit": 2})
    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == [str(item.symbols["ZQXP6C1"]),
                                              str(item.symbols["ZQXP6C2"])]
