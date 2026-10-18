"""P6a news ingest against real PostgreSQL (rolled back): dedupe on
(provider, external_id), URL / similar-headline merging, symbol filtering,
status and origin rules, author sources, and the relevance tiers."""

import os
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models_registry  # noqa: F401
from app.modules.market.models import Security
from app.modules.news import ingest as ingest_mod
from app.modules.news.ingest import ingest_alpaca
from app.modules.news.models import NewsItem, NewsItemSource, NewsItemSymbol, NewsSource
from app.modules.news.relevance import relevance_map
from app.modules.simledger.models import Recommendation, SimAccount, SimPosition
from app.modules.strategy_groups.models import StrategyGroup
from app.modules.watchlist.models import Watchlist, WatchlistItem
from tests._pg_rollback import pg_session_rollback  # noqa: F401

BOOT = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)
LIVE_AT = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
EQUITIES = ("ZQXA", "ZQXB", "ZQXC", "ZQXD", "ZQXE", "ZQXF")
RELEVANT = {"ZQXA": (1, "shortlist"), "ZQXB": (2, "pool")}
OUR_IDS = tuple(str(i) for i in range(1, 10))
COUNTED = (NewsItem, NewsItemSource, NewsItemSymbol)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw(id_, headline="ZQXA beats Q3 estimates", *, symbols=("ZQXA",), author="Jane Doe",
         url=None, at=LIVE_AT, summary="", updated=None):
    return {"id": id_, "headline": headline, "author": author, "summary": summary,
            "content": "", "url": url if url is not None else f"https://benzinga.com/n/{id_}",
            "symbols": list(symbols), "created_at": _iso(at),
            "updated_at": _iso(updated or at), "source": "benzinga"}


@pytest_asyncio.fixture
async def db(pg_session_rollback):
    for code in EQUITIES:
        pg_session_rollback.add(Security(code=code, asset_type="stock", tradable=True,
                                         first_seen=date(2026, 1, 2),
                                         last_seen=date(2026, 10, 16)))
    pg_session_rollback.add(Security(code="ZQXI", asset_type="warrant", tradable=True,
                                     first_seen=date(2026, 1, 2), last_seen=date(2026, 10, 16)))
    await pg_session_rollback.flush()
    pg_session_rollback.info["baseline"] = {
        model: await _count(pg_session_rollback, model) for model in COUNTED}
    return pg_session_rollback


async def _ingest(db, raws, *, relevance=RELEVANT, bootstrap_at=BOOT, now=NOW):
    return await ingest_alpaca(db, raws, bootstrap_at=bootstrap_at, now=now,
                               relevance=relevance)


def _our_sources():
    return select(NewsItemSource).where(NewsItemSource.provider == "alpaca",
                                        NewsItemSource.external_id.in_(OUR_IDS))


def _our_news_ids():
    return _our_sources().with_only_columns(NewsItemSource.news_id)


async def _items(db):
    rows = (await db.execute(select(NewsItem).where(NewsItem.id.in_(_our_news_ids()))
                             .order_by(NewsItem.published_at,
                                       NewsItem.headline))).scalars().all()
    assert len(rows) == await _added(db, NewsItem)
    return rows


async def _count(db, model):
    return (await db.execute(select(func.count()).select_from(model))).scalar()


async def _added(db, model):
    return await _count(db, model) - db.info["baseline"][model]


async def _item_for(db, external_id) -> NewsItem:
    return (await db.execute(
        select(NewsItem).join(NewsItemSource, NewsItemSource.news_id == NewsItem.id)
        .where(NewsItemSource.provider == "alpaca",
               NewsItemSource.external_id == str(external_id)))).scalar_one()


@pytest.mark.asyncio
async def test_same_id_twice_updates_instead_of_inserting(db):
    first = await _ingest(db, [_raw(1)])
    assert (first["new"], first["pending"], first["live"]) == (1, 1, 1)
    second = await _ingest(db, [_raw(1, updated=LIVE_AT + timedelta(minutes=5))])
    assert (second["new"], second["updated"], second["fetched"]) == (0, 1, 1)
    assert await _added(db, NewsItem) == 1
    assert await _added(db, NewsItemSource) == 1
    assert await _added(db, NewsItemSymbol) == 1
    src = (await db.execute(_our_sources())).scalar_one()
    assert src.updated_at_src == LIVE_AT + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_changed_headline_updates_text_but_not_status(db):
    await _ingest(db, [_raw(1)])
    item = await _item_for(db, 1)
    item.status, item.extracted_at = "no_claim", NOW
    await db.flush()
    later = NOW + timedelta(hours=1)
    await _ingest(db, [_raw(1, "ZQXA beats Q3 estimates, raises guidance", summary="new",
                            updated=LIVE_AT + timedelta(minutes=30))], now=later)
    item = await _item_for(db, 1)
    assert item.status == "no_claim"
    assert item.headline == "ZQXA beats Q3 estimates, raises guidance"
    assert item.headline_norm == "zqxa beats q3 estimates raises guidance"
    assert item.summary == "new"
    assert item.updated_at == later


def _merged_pair(updated_a=None, updated_b=None, headline_a="ZQXA Raises FY Guidance",
                 headline_b="ZQXA raises FY guidance.", summary_a="sumA"):
    return [_raw(1, headline_a, author="Jane Doe", summary=summary_a, updated=updated_a),
            _raw(2, headline_b, author="Benzinga Newsdesk", summary="", updated=updated_b)]


@pytest.mark.asyncio
async def test_merged_item_text_is_stable_on_refetch(db):
    first = await _ingest(db, _merged_pair())
    assert (first["new"], first["merged"]) == (1, 1)
    (item,) = await _items(db)
    stamped = item.updated_at
    second = await _ingest(db, _merged_pair(), now=NOW + timedelta(hours=1))
    assert second["updated"] == 2
    (item,) = await _items(db)
    assert (item.headline, item.summary) == ("ZQXA Raises FY Guidance", "sumA")
    assert item.headline_norm == "zqxa raises fy guidance"
    assert item.updated_at == stamped


@pytest.mark.asyncio
async def test_secondary_source_edit_leaves_item_text_alone(db):
    await _ingest(db, _merged_pair())
    edited = LIVE_AT + timedelta(minutes=20)
    await _ingest(db, _merged_pair(updated_b=edited, headline_b="ZQXA RAISES FY GUIDANCE!!"),
                  now=NOW + timedelta(hours=1))
    (item,) = await _items(db)
    assert (item.headline, item.summary) == ("ZQXA Raises FY Guidance", "sumA")
    src = (await db.execute(select(NewsItemSource).where(
        NewsItemSource.external_id == "2"))).scalar_one()
    assert src.updated_at_src == edited


@pytest.mark.asyncio
async def test_text_change_without_provider_edit_is_ignored(db):
    await _ingest(db, _merged_pair())
    await _ingest(db, _merged_pair(headline_a="ZQXA Raises FY Guidance Again"),
                  now=NOW + timedelta(hours=1))
    (item,) = await _items(db)
    assert item.headline == "ZQXA Raises FY Guidance"


@pytest.mark.asyncio
async def test_primary_source_edit_updates_item_but_never_blanks_summary(db):
    await _ingest(db, _merged_pair())
    later = NOW + timedelta(hours=1)
    await _ingest(db, _merged_pair(updated_a=LIVE_AT + timedelta(minutes=20),
                                   headline_a="ZQXA Raises FY Guidance Again",
                                   summary_a=""), now=later)
    (item,) = await _items(db)
    assert (item.headline, item.summary) == ("ZQXA Raises FY Guidance Again", "sumA")
    assert item.headline_norm == "zqxa raises fy guidance again"
    assert item.updated_at == later


def _malformed():
    good = _raw(9)
    return [
        "not a dict",
        {k: v for k, v in good.items() if k != "id"},
        {**good, "id": None},
        {**good, "id": True},
        {**good, "id": "x" * 101},
        {**good, "id": "t6\x00id"},
        {k: v for k, v in good.items() if k != "created_at"},
        {**good, "created_at": "yesterday"},
        {**good, "created_at": "2026-10-17T12:00:00"},
        {**good, "updated_at": None},
        {**good, "updated_at": 1760702400},
        {k: v for k, v in good.items() if k != "headline"},
        {**good, "headline": "   "},
        {**good, "headline": 42},
        {**good, "symbols": "ZQXA"},
        {**good, "author": ["Jane"]},
        {**good, "url": 7},
    ]


@pytest.mark.parametrize("bad", _malformed())
@pytest.mark.asyncio
async def test_malformed_article_is_skipped_and_the_rest_ingest(db, bad, caplog):
    counts = await _ingest(db, [_raw(1), bad, _raw(2, "Separate ZQXB story", symbols=("ZQXB",))])
    assert (counts["fetched"], counts["new"], counts["invalid"]) == (3, 2, 1)
    assert await _added(db, NewsItem) == 2
    assert "skipped malformed alpaca article" in caplog.text


@pytest.mark.asyncio
async def test_nul_bytes_are_stripped(db):
    counts = await _ingest(db, [_raw(1, "ZQXA beats\x00 Q3", author="Jane\x00 Doe",
                                     summary="s\x00um", url="https://benzinga.com/n/1\x00")])
    assert (counts["new"], counts["invalid"]) == (1, 0)
    (item,) = await _items(db)
    assert (item.headline, item.summary) == ("ZQXA beats Q3", "sum")
    src = (await db.execute(_our_sources())).scalar_one()
    assert await _added(db, NewsItemSource) == 1
    assert src.url == "https://benzinga.com/n/1"
    author = (await db.execute(select(NewsSource.name).where(
        NewsSource.id == src.source_id))).scalar_one()
    assert author == "benzinga/Jane Doe"


@pytest.mark.asyncio
async def test_nul_only_headline_is_invalid(db):
    counts = await _ingest(db, [_raw(1, "\x00\x00"), _raw(2, "Fine ZQXB", symbols=("ZQXB",))])
    assert (counts["new"], counts["invalid"]) == (1, 1)


@pytest.mark.asyncio
async def test_database_rejected_article_costs_only_itself(db, monkeypatch, caplog):
    await _ingest(db, [_raw(1, "ZQXA original headline")])
    monkeypatch.setattr(ingest_mod, "_clean", lambda value: value)
    edit = LIVE_AT + timedelta(minutes=10)
    counts = await _ingest(db, [
        _raw(1, "ZQXA edited\x00", updated=edit),
        _raw(2, "Rejected\x00 ZQXB story", author="New Writer", symbols=("ZQXB",)),
        _raw(3, "Accepted ZQXC story", author="New Writer", symbols=("ZQXC",)),
        _raw(1, "ZQXA edited cleanly", updated=edit),
    ], now=NOW + timedelta(hours=1))
    assert (counts["invalid"], counts["new"], counts["updated"]) == (2, 1, 1)
    assert (await _item_for(db, 1)).headline == "ZQXA edited cleanly"
    assert await _added(db, NewsItem) == 2
    writer = (await db.execute(select(NewsSource).where(
        NewsSource.name == "benzinga/New Writer"))).scalar_one()
    src3 = (await db.execute(select(NewsItemSource).where(
        NewsItemSource.external_id == "3"))).scalar_one()
    assert src3.source_id == writer.id
    assert "database rejected alpaca article id=2" in caplog.text


@pytest.mark.asyncio
async def test_same_normalized_url_merges(db):
    counts = await _ingest(db, [
        _raw(1, "ZQXA beats", url="https://www.benzinga.com/n/x/"),
        _raw(2, "Totally different words", author="Other Writer",
             url="https://benzinga.com/n/x?utm_source=feed"),
    ])
    assert (counts["new"], counts["merged"]) == (1, 1)
    assert await _added(db, NewsItem) == 1
    assert await _added(db, NewsItemSource) == 2


@pytest.mark.asyncio
async def test_similar_headline_merges_across_sources(db):
    counts = await _ingest(db, [
        _raw(1, "ZQXA Raises FY Guidance Above Estimates", author="Jane Doe",
             at=LIVE_AT + timedelta(hours=2)),
        _raw(2, "ZQXA raises FY guidance above estimates.", author="Benzinga Newsdesk",
             at=LIVE_AT),
    ])
    assert (counts["new"], counts["merged"]) == (1, 1)
    (item,) = await _items(db)
    assert item.published_at == LIVE_AT
    sources = (await db.execute(_our_sources().with_only_columns(
        NewsItemSource.source_id))).scalars().all()
    assert len(set(sources)) == 2


@pytest.mark.asyncio
async def test_same_source_same_template_is_not_merged(db):
    counts = await _ingest(db, [
        _raw(1, "ZQXA Options Activity Spikes Today", author="Benzinga Insights"),
        _raw(2, "ZQXA Options Activity Spikes Today", author="Benzinga Insights",
             at=LIVE_AT + timedelta(hours=3)),
    ])
    assert (counts["new"], counts["merged"]) == (2, 0)
    assert await _added(db, NewsItem) == 2


@pytest.mark.asyncio
async def test_similar_headline_without_shared_symbol_is_not_merged(db):
    counts = await _ingest(db, [
        _raw(1, "Shares rally after earnings beat", symbols=("ZQXA",)),
        _raw(2, "Shares rally after earnings beat", symbols=("ZQXB",), author="Other"),
    ])
    assert (counts["new"], counts["merged"]) == (2, 0)


@pytest.mark.asyncio
async def test_crypto_and_unknown_symbols_are_dropped(db):
    await _ingest(db, [_raw(1, symbols=("BTCUSD", "BTC/USD", "zqxa", "ZQXI"))])
    symbols = (await db.execute(select(NewsItemSymbol.symbol).where(
        NewsItemSymbol.news_id.in_(_our_news_ids())))).scalars().all()
    assert symbols == ["ZQXA"]
    assert await _added(db, NewsItemSymbol) == 1


@pytest.mark.asyncio
async def test_only_crypto_symbols_is_skipped(db):
    counts = await _ingest(db, [_raw(1, symbols=("BTCUSD",))])
    (item,) = await _items(db)
    assert (item.status, item.skip_reason, item.priority) == ("skipped",
                                                               "no_us_equity_symbol", None)
    assert counts["skipped"] == 1


@pytest.mark.asyncio
async def test_six_symbols_is_multi_symbol(db):
    await _ingest(db, [_raw(1, symbols=EQUITIES)])
    (item,) = await _items(db)
    assert (item.status, item.skip_reason) == ("skipped", "multi_symbol")
    assert await _added(db, NewsItemSymbol) == 6


@pytest.mark.asyncio
async def test_unknown_symbols_are_filtered_before_counting(db):
    await _ingest(db, [_raw(1, symbols=("ZQXA", "ZQXB", "ZQXC", "QQQZZ1", "QQQZZ2",
                                        "QQQZZ3"))])
    (item,) = await _items(db)
    assert (item.status, item.priority) == ("pending", 1)
    assert await _added(db, NewsItemSymbol) == 3


@pytest.mark.asyncio
async def test_not_relevant_is_irrelevant(db):
    counts = await _ingest(db, [_raw(1, symbols=("ZQXC",))])
    (item,) = await _items(db)
    assert (item.status, item.skip_reason) == ("irrelevant", "not_relevant")
    assert counts["irrelevant"] == 1
    row = (await db.execute(select(NewsItemSymbol).where(
        NewsItemSymbol.news_id.in_(_our_news_ids())))).scalar_one()
    assert row.relevance == "none"
    assert await _added(db, NewsItemSymbol) == 1


@pytest.mark.asyncio
async def test_priority_is_lowest_tier_and_labels_per_symbol(db):
    await _ingest(db, [_raw(1, symbols=("ZQXB", "ZQXA", "ZQXC"))])
    (item,) = await _items(db)
    assert (item.status, item.priority, item.origin) == ("pending", 1, "live")
    labels = dict((await db.execute(select(NewsItemSymbol.symbol, NewsItemSymbol.relevance)
                                    .where(NewsItemSymbol.news_id.in_(_our_news_ids())))).all())
    assert labels == {"ZQXA": "shortlist", "ZQXB": "pool", "ZQXC": "none"}


@pytest.mark.asyncio
async def test_backfill_is_always_skipped_raw(db):
    counts = await _ingest(db, [
        _raw(1, symbols=("ZQXA",), at=BOOT - timedelta(hours=30)),
        _raw(2, "Another ZQXB story", symbols=("ZQXB",), at=BOOT - timedelta(hours=2)),
        _raw(3, "Crypto", symbols=("BTCUSD",), at=BOOT - timedelta(hours=1)),
    ])
    items = await _items(db)
    assert {(i.origin, i.status, i.skip_reason) for i in items} == {
        ("backfill", "skipped", "backfill_raw")}
    assert (counts["backfill"], counts["live"], counts["skipped"]) == (3, 0, 3)


@pytest.mark.asyncio
async def test_origin_boundary_at_bootstrap(db):
    await _ingest(db, [
        _raw(1, "Before boot", at=BOOT - timedelta(seconds=1)),
        _raw(2, "Exactly at boot", symbols=("ZQXB",), at=BOOT),
    ])
    assert (await _item_for(db, 1)).origin == "backfill"
    at_boot = await _item_for(db, 2)
    assert (at_boot.origin, at_boot.status) == ("live", "pending")


@pytest.mark.asyncio
async def test_disabled_author_priority_is_pushed_back(db):
    platform = (await db.execute(select(NewsSource).where(
        NewsSource.kind == "platform", NewsSource.name == "benzinga"))).scalar_one()
    db.add(NewsSource(kind="author", name="benzinga/Muted Writer", parent_id=platform.id,
                      enabled=False, disabled_at=NOW, disabled_by="manual"))
    await db.flush()
    await _ingest(db, [_raw(1, author="Muted Writer")])
    (item,) = await _items(db)
    assert item.status == "pending" and item.priority >= 10


@pytest.mark.asyncio
async def test_empty_author_attaches_to_platform(db):
    await _ingest(db, [_raw(1, author="   "), _raw(2, "Other ZQXB news", author=None,
                                                    symbols=("ZQXB",))])
    rows = (await db.execute(
        select(NewsSource.kind, NewsSource.name)
        .join(NewsItemSource, NewsItemSource.source_id == NewsSource.id)
        .where(NewsItemSource.news_id.in_(_our_news_ids())))).all()
    assert set(rows) == {("platform", "benzinga")}
    assert await _added(db, NewsItemSource) == 2


@pytest.mark.asyncio
async def test_author_source_is_created_once_under_platform(db):
    await _ingest(db, [_raw(1, author=" Jane Doe "), _raw(2, "Unrelated ZQXB", symbols=("ZQXB",))])
    authors = (await db.execute(select(NewsSource).where(
        NewsSource.name == "benzinga/Jane Doe"))).scalars().all()
    assert len(authors) == 1 and authors[0].kind == "author"
    platform = (await db.execute(select(NewsSource).where(
        NewsSource.kind == "platform"))).scalar_one()
    assert authors[0].parent_id == platform.id


@pytest.mark.asyncio
async def test_summary_is_capped_at_2000(db):
    await _ingest(db, [_raw(1, summary="x" * 5000)])
    (item,) = await _items(db)
    assert len(item.summary) == 2000


async def _news_item_count() -> int:
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text("select count(*) from news_items"))).scalar()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def news_items_unchanged():
    before = await _news_item_count()
    yield before
    assert await _news_item_count() == before


@pytest.mark.asyncio
async def test_rollback_fixture_discards_committed_rows(news_items_unchanged,
                                                        pg_session_rollback):
    pg_session_rollback.add(NewsItem(origin="live", headline="fixture probe",
                                     headline_norm="fixture probe", published_at=NOW,
                                     status="skipped", skip_reason="probe"))
    await pg_session_rollback.commit()
    assert await _count(pg_session_rollback, NewsItem) == news_items_unchanged + 1
    assert await _news_item_count() == news_items_unchanged


@pytest.mark.asyncio
async def test_relevance_tiers(db):
    user_id = (await db.execute(text("select id from users limit 1"))).scalar_one()
    account = SimAccount(id=uuid4(), user_id=user_id, name=f"news-test-{uuid4().hex[:8]}",
                         is_system=True)
    db.add(account)
    await db.flush()
    group = StrategyGroup(id=uuid4(), name=f"news-test-{uuid4().hex[:8]}",
                          strategy_key="cat.v1.composite", status="paused",
                          sim_account_id=account.id)
    db.add(group)
    await db.flush()
    db.add(SimPosition(account_id=account.id, symbol="ZQXH", shares=1, avg_cost=10, stop=9,
                       r_unit=1, high_water=10, entry_date=date(2026, 10, 15)))
    for symbol, day, rank in (("ZQXOLD", date(2026, 10, 15), 1), ("ZQXH", date(2026, 10, 16), 1),
                              ("ZQXS", date(2026, 10, 16), 2), ("ZQXP", date(2026, 10, 16), None)):
        db.add(Recommendation(group_id=group.id, symbol=symbol, trade_date=day,
                              direction="up", confidence=70, shortlist_rank=rank, phase="up"))
    watchlist = Watchlist(id=uuid4(), user_id=user_id, name="news-test")
    db.add(watchlist)
    await db.flush()
    db.add(WatchlistItem(watchlist_id=watchlist.id, symbol="zqxw", market_type="stock"))
    await db.flush()

    paused = await relevance_map(db)
    assert paused["ZQXH"] == (0, "held")
    assert paused["ZQXS"] == (2, "pool")
    assert paused["ZQXP"] == (2, "pool")
    assert paused["ZQXW"] == (2, "watchlist")
    assert "ZQXOLD" not in paused

    group.status = "active"
    await db.flush()
    active = await relevance_map(db)
    assert active["ZQXS"] == (1, "shortlist")
    assert active["ZQXH"] == (0, "held")

    group.status, group.archived_at = "archived", NOW
    await db.flush()
    archived = await relevance_map(db)
    assert not {"ZQXH", "ZQXS", "ZQXP"} & set(archived)
