"""Turn raw Alpaca news articles into deduplicated news rows (no LLM)."""

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import exists, select
from sqlalchemy.exc import StatementError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.modules.market.models import Security
from app.modules.news.models import (
    PLATFORM_BENZINGA, NewsItem, NewsItemSource, NewsItemSymbol, NewsSource
)
from app.modules.news.text import SYMBOL_RE, normalize_headline, similar, url_hash

logger = logging.getLogger(__name__)

PROVIDER = "alpaca"
_EQUITY_TYPES = ("stock", "adr", "etf")
_SUMMARY_MAX = 2000
_EXTERNAL_ID_MAX = 100
_DISABLED_PRIORITY_PENALTY = 10
COUNT_KEYS = ("fetched", "new", "merged", "updated", "pending", "irrelevant", "skipped",
              "backfill", "live", "invalid")


async def platform_source(db) -> NewsSource:
    return (await db.execute(
        select(NewsSource).where(NewsSource.kind == "platform",
                                 NewsSource.name == PLATFORM_BENZINGA)
    )).scalar_one()


async def ensure_author_source(db, platform_row: NewsSource, author: str | None) -> NewsSource:
    author = (author or "").strip()
    if not author:
        return platform_row
    name = f"{PLATFORM_BENZINGA}/{author[:180]}"
    await db.execute(
        pg_insert(NewsSource)
        .values(id=uuid4(), kind="author", name=name, parent_id=platform_row.id)
        .on_conflict_do_nothing(index_elements=["kind", "name"])
    )
    return (await db.execute(
        select(NewsSource).where(NewsSource.kind == "author", NewsSource.name == name)
    )).scalar_one()


def _aware_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None


def _clean(value):
    return value.replace("\x00", "") if isinstance(value, str) else value


def _parse(raw) -> dict | None:
    """One Alpaca article as the fields ingest uses, or None when it is malformed
    (a bad article must not fail the whole tick and wedge the cursor on it)."""
    if not isinstance(raw, dict):
        return None
    raw_id = raw.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
        return None
    external_id = str(raw_id).strip()
    headline = _clean(raw.get("headline"))
    symbols = raw.get("symbols")
    published_at = _aware_ts(raw.get("created_at"))
    updated_at_src = _aware_ts(raw.get("updated_at"))
    if (not external_id or len(external_id) > _EXTERNAL_ID_MAX or "\x00" in external_id
            or not isinstance(headline, str) or not headline.strip()
            or not isinstance(symbols, list)
            or published_at is None or updated_at_src is None
            or not all(isinstance(raw.get(k), (str, type(None)))
                       for k in ("author", "summary", "url"))):
        return None
    summary, author, url = (_clean(raw.get(k)) for k in ("summary", "author", "url"))
    seen: dict[str, None] = {}
    for symbol in symbols:
        symbol = str(symbol).upper()
        if SYMBOL_RE.match(symbol):
            seen.setdefault(symbol)
    return {"external_id": external_id, "headline": headline,
            "headline_norm": normalize_headline(headline),
            "summary": (summary or "")[:_SUMMARY_MAX] or None,
            "author": (author or "").strip(), "url": url or None,
            "symbols": list(seen), "published_at": published_at,
            "updated_at_src": updated_at_src}


async def _refresh_known(db, src_id, a: dict, now: datetime) -> None:
    src_row = await db.get(NewsItemSource, src_id)
    edited = a["updated_at_src"] != src_row.updated_at_src
    src_row.updated_at_src = a["updated_at_src"]
    if edited and await _is_primary(db, src_row):
        item = await db.get(NewsItem, src_row.news_id)
        summary = a["summary"] or item.summary
        if (item.headline, item.headline_norm, item.summary) != (
                a["headline"], a["headline_norm"], summary):
            item.headline, item.headline_norm = a["headline"], a["headline_norm"]
            item.summary = summary
            item.updated_at = now


async def _is_primary(db, src_row: NewsItemSource) -> bool:
    primary_id = (await db.execute(
        select(NewsItemSource.id).where(NewsItemSource.news_id == src_row.news_id)
        .order_by(NewsItemSource.fetched_at, NewsItemSource.id).limit(1)
    )).scalar()
    return primary_id == src_row.id


async def _us_equities(db, candidates: set[str]) -> set[str]:
    if not candidates:
        return set()
    return set((await db.execute(
        select(Security.code).where(Security.market == "us",
                                    Security.status == "active",
                                    Security.asset_type.in_(_EQUITY_TYPES),
                                    Security.code.in_(candidates))
    )).scalars())


async def _merge_target(db, *, uhash, headline_norm, published_at, symbols, source_id):
    if uhash:
        news_id = (await db.execute(
            select(NewsItemSource.news_id).where(NewsItemSource.url_hash == uhash).limit(1)
        )).scalar()
        if news_id is not None:
            return news_id
    if not symbols:
        return None
    window = timedelta(hours=settings.NEWS_MERGE_WINDOW_HOURS)
    same_source = exists().where(NewsItemSource.news_id == NewsItem.id,
                                 NewsItemSource.source_id == source_id)
    candidates = (await db.execute(
        select(NewsItem.id, NewsItem.headline_norm).distinct()
        .join(NewsItemSymbol, NewsItemSymbol.news_id == NewsItem.id)
        .where(NewsItemSymbol.symbol.in_(symbols),
               NewsItem.published_at.between(published_at - window, published_at + window),
               ~same_source)
    )).all()
    best, best_ratio = None, settings.NEWS_MERGE_MIN_RATIO
    for news_id, norm in candidates:
        ratio = similar(headline_norm, norm)
        if ratio >= best_ratio and (best is None or ratio > best_ratio):
            best, best_ratio = news_id, ratio
    return best


async def ingest_alpaca(db, raw_items: list[dict], *, bootstrap_at: datetime, now: datetime,
                        relevance: dict[str, tuple[int, str]]) -> dict:
    """Upsert one fetch worth of Alpaca articles; a malformed article is counted
    as ``invalid`` and skipped. A known ``(provider, external_id)`` refreshes
    the item's text only when the provider edited it AND it is the item's
    primary (first-linked) source, never blanking a summary; status is never
    reset, so an edit never triggers a re-extract. A new article first tries
    to merge into an existing story (same normalised URL, else a near-identical
    headline from a DIFFERENT source sharing a symbol within the merge window),
    otherwise it becomes a new item whose origin is decided per article against
    ``bootstrap_at`` — never per tick — so a backfill spread over several
    truncated ticks is never scored as live."""
    counts = dict.fromkeys(COUNT_KEYS, 0)
    counts["fetched"] = len(raw_items)
    articles = []
    for raw in raw_items:
        article = _parse(raw)
        if article is None:
            counts["invalid"] += 1
            logger.warning("news ingest: skipped malformed alpaca article id=%r",
                           raw.get("id") if isinstance(raw, dict) else None)
        else:
            articles.append(article)
    if not articles:
        return counts

    platform = await platform_source(db)
    authors: dict[str, NewsSource] = {}
    equities = await _us_equities(db, {s for a in articles for s in a["symbols"]})
    known = {row.external_id: row.id for row in (await db.execute(
        select(NewsItemSource).where(
            NewsItemSource.provider == PROVIDER,
            NewsItemSource.external_id.in_([a["external_id"] for a in articles]))
    )).scalars()}

    for a in articles:
        tally: list[str] = []
        created = None
        try:
            # why one savepoint per article: a value PostgreSQL rejects must cost
            # only that article, not the whole tick (which would wedge the cursor)
            async with db.begin_nested():
                if a["external_id"] in known:
                    await _refresh_known(db, known[a["external_id"]], a, now)
                    tally.append("updated")
                else:
                    source = (authors.get(a["author"])
                              or await ensure_author_source(db, platform, a["author"]))
                    tally, src_row = await _add_article(db, a, source, equities=equities,
                                                        bootstrap_at=bootstrap_at,
                                                        relevance=relevance)
                    created = (source, src_row.id)
                await db.flush()
        except StatementError as e:
            counts["invalid"] += 1
            logger.warning("news ingest: database rejected alpaca article id=%s (%s)",
                           a["external_id"], type(e.orig or e).__name__)
            continue
        for key in tally:
            counts[key] += 1
        if created is not None:
            authors[a["author"]], known[a["external_id"]] = created
    return counts


async def _add_article(db, a: dict, source: NewsSource, *, equities, bootstrap_at,
                       relevance) -> tuple[list[str], NewsItemSource]:
    published_at, symbols = a["published_at"], [s for s in a["symbols"] if s in equities]
    uhash = url_hash(a["url"])
    news_id = await _merge_target(db, uhash=uhash, headline_norm=a["headline_norm"],
                                  published_at=published_at, symbols=symbols,
                                  source_id=source.id)
    if news_id is not None:
        item = await db.get(NewsItem, news_id)
        item.published_at = min(item.published_at, published_at)
        tally = ["merged"]
    else:
        news_id = uuid4()
        status, skip_reason, priority = "pending", None, None
        origin = "backfill" if published_at < bootstrap_at else "live"
        tiers = [relevance[s][0] for s in symbols if s in relevance]
        if origin == "backfill":
            status, skip_reason = "skipped", "backfill_raw"
        elif not symbols:
            status, skip_reason = "skipped", "no_us_equity_symbol"
        elif len(symbols) > settings.NEWS_MAX_SYMBOLS:
            status, skip_reason = "skipped", "multi_symbol"
        elif not tiers:
            status, skip_reason = "irrelevant", "not_relevant"
        else:
            priority = min(tiers) + (0 if source.enabled else _DISABLED_PRIORITY_PENALTY)
        db.add(NewsItem(id=news_id, origin=origin, headline=a["headline"],
                        summary=a["summary"], headline_norm=a["headline_norm"],
                        published_at=published_at, status=status,
                        skip_reason=skip_reason, priority=priority))
        for s in symbols:
            db.add(NewsItemSymbol(news_id=news_id, symbol=s,
                                  relevance=relevance[s][1] if s in relevance else "none"))
        tally = ["new", origin, status]

    # why fetched_at is stamped per row: it orders an item's sources, and the
    # earliest one (the article that created the item) is the only one allowed
    # to rewrite the item's text; now() would tie every row of one transaction.
    src_row = NewsItemSource(id=uuid4(), news_id=news_id, source_id=source.id,
                             provider=PROVIDER, external_id=a["external_id"],
                             url=a["url"], url_hash=uhash, published_at=published_at,
                             updated_at_src=a["updated_at_src"],
                             fetched_at=datetime.now(timezone.utc))
    db.add(src_row)
    return tally, src_row
