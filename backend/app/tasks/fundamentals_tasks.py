"""Fundamentals cache refresh Celery tasks (Phase 3 FA — TASKS-fundamentals-refresh).

Three periodic tasks fill the two slow-moving cache tables from the Finnhub FA
client so the equity pipeline reads Postgres instead of burning the free-tier
quota every cycle:

  - fundamentals.refresh_company_profiles   (weekly)  -> company_fundamentals
      profile (name/industry), shares outstanding, market cap, avg volume,
      S&P 500 membership (best-effort), and the historical financials blob.
  - fundamentals.refresh_earnings_calendar  (daily, pre-market) -> earnings_calendar
      future + recent report dates with EPS/revenue estimates (and actuals when
      already reported).
  - fundamentals.refresh_financials_on_earnings (daily) -> earnings_calendar +
      company_fundamentals — fills the `actual` figures for symbols that reported
      in the last few days, and refreshes the historical financials blob.

Guardrails (see task description):
  * Scope is limited to symbols on a watchlist with market_type == "stock" — we
    never scan the whole market and blow the free quota. A hard MAX_SYMBOLS cap
    is applied on top.
  * No key / rate-limited / any HTTP failure: the FinnhubClient already returns
    []/None instead of raising, and every task checks `client.enabled` first and
    skips cleanly with a log line. Nothing crashes.
  * Writes are UPSERTs on the unique keys (symbol / symbol+report_date) so a
    re-run merges instead of duplicating. Only fields we actually received are
    written, so a partial/empty API response never overwrites good cached data
    with NULLs ("不写坏数据").
  * Requests are throttled (PER_CALL_SLEEP per API call) to respect the 60
    calls/min free tier; the schedule intervals are deliberately coarse.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from celery import shared_task
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.celery_database import CeleryAsyncSessionLocal
from app.modules.fundamentals.finnhub_client import get_finnhub_client
from app.modules.fundamentals.models import CompanyFundamentals, EarningsCalendar
from app.modules.watchlist.models import WatchlistItem
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)

# Safety cap: never fan out to more than this many symbols in one run, even if a
# watchlist somehow grows huge — protects the free-tier quota.
MAX_SYMBOLS = 50

# Seconds to sleep per outbound API call. 60 calls/min free tier => >=1s/call.
PER_CALL_SLEEP = 1.2

# Calendar window: how far back / forward to pull report dates.
CALENDAR_PAST_DAYS = 7
CALENDAR_FUTURE_DAYS = 90

# How many days back to scan for freshly-reported actuals.
EARNINGS_LOOKBACK_DAYS = 3


# ---- small pure helpers (unit-tested) -------------------------------------


def _set(target: dict[str, Any], key: str, value: Any) -> None:
    """Assign only non-None values — a missing datum must not clobber cache."""
    if value is not None:
        target[key] = value


def _dec(value: Any) -> Optional[Decimal]:
    """Best-effort Decimal conversion. None/blank/garbage -> None (never raises)."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _millions(value: Any) -> Optional[Decimal]:
    """Finnhub reports shares / market cap / volume in millions — expand to units."""
    dec = _dec(value)
    if dec is None:
        return None
    return dec * Decimal("1000000")


def _parse_date(value: Any) -> Optional[date]:
    """Parse a Finnhub YYYY-MM-DD date string. Bad input -> None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _map_profile_row(
    symbol: str,
    profile: Optional[dict[str, Any]],
    metric: Optional[dict[str, Any]],
    actuals: Optional[list[dict[str, Any]]],
    is_sp500: Optional[bool] = None,
) -> dict[str, Any]:
    """Map Finnhub profile2 + metric + earnings-history into company_fundamentals
    column values. Only fields we actually have are included."""
    profile = profile or {}
    metric_root = metric or {}
    m = metric_root.get("metric") if isinstance(metric_root.get("metric"), dict) else {}

    values: dict[str, Any] = {"symbol": symbol}
    _set(values, "name", profile.get("name"))
    _set(values, "industry", profile.get("finnhubIndustry"))
    _set(values, "shares_outstanding", _millions(profile.get("shareOutstanding")))
    _set(values, "market_cap", _millions(profile.get("marketCapitalization")))
    _set(
        values,
        "avg_volume",
        _millions(
            m.get("10DayAverageTradingVolume") or m.get("3MonthAverageTradingVolume")
        ),
    )
    if is_sp500 is not None:
        values["is_sp500"] = is_sp500
    if isinstance(actuals, list) and actuals:
        values["historical_financials"] = actuals
    return values


def _map_calendar_row(row: Any) -> Optional[dict[str, Any]]:
    """Map a Finnhub earnings-calendar row into earnings_calendar column values.

    Returns None when the row lacks the required (symbol, report_date) key."""
    if not isinstance(row, dict):
        return None
    symbol = row.get("symbol")
    report_date = _parse_date(row.get("date"))
    if not symbol or report_date is None:
        return None

    values: dict[str, Any] = {"symbol": symbol, "report_date": report_date}
    _set(values, "time", row.get("hour"))
    _set(values, "eps_estimate", _dec(row.get("epsEstimate")))
    _set(values, "rev_estimate", _dec(row.get("revenueEstimate")))
    _set(values, "eps_actual", _dec(row.get("epsActual")))
    _set(values, "rev_actual", _dec(row.get("revenueActual")))

    has_actual = row.get("epsActual") is not None or row.get("revenueActual") is not None
    values["status"] = "reported" if has_actual else "scheduled"
    return values


def _throttle(calls: int) -> None:
    """Sleep proportional to the API calls just made, to stay under the quota."""
    if calls > 0:
        time.sleep(calls * PER_CALL_SLEEP)


# ---- DB helpers -----------------------------------------------------------


async def _watchlist_stock_symbols(session) -> list[str]:
    """Distinct equity symbols currently on any watchlist (capped at MAX_SYMBOLS)."""
    stmt = (
        select(WatchlistItem.symbol)
        .where(WatchlistItem.market_type == "stock")
        .distinct()
    )
    rows = (await session.execute(stmt)).scalars().all()
    symbols = sorted({s for s in rows if s})
    if len(symbols) > MAX_SYMBOLS:
        logger.warning(
            "Watchlist has %s stock symbols; capping refresh at %s",
            len(symbols),
            MAX_SYMBOLS,
        )
        symbols = symbols[:MAX_SYMBOLS]
    return symbols


async def _upsert(session, model, values: dict[str, Any], constraint: str) -> None:
    """UPSERT `values` into `model` on `constraint`, updating only supplied columns.

    None-valued fields are dropped upstream, so this never writes a NULL over a
    previously-good cached value. `symbol` (and `report_date` for the calendar,
    both part of the conflict key) are never part of the update set."""
    clean = {k: v for k, v in values.items() if v is not None}
    if "symbol" not in clean:
        return

    key_cols = {"symbol", "report_date"}
    stmt = pg_insert(model).values(**clean, last_refreshed_at=func.now())
    update_cols = {
        k: getattr(stmt.excluded, k) for k in clean if k not in key_cols
    }
    update_cols["last_refreshed_at"] = func.now()
    update_cols["updated_at"] = func.now()
    stmt = stmt.on_conflict_do_update(constraint=constraint, set_=update_cols)
    await session.execute(stmt)


# ---- task cores (testable, take session + client) -------------------------


async def _refresh_company_profiles(session, client) -> int:
    """Refresh company_fundamentals for every watchlist equity symbol."""
    if not client.enabled:
        logger.warning(
            "refresh_company_profiles: Finnhub not configured; skipping cleanly"
        )
        return 0

    symbols = await _watchlist_stock_symbols(session)
    if not symbols:
        logger.info("refresh_company_profiles: no watchlist stock symbols; nothing to do")
        return 0

    # One extra call for the whole run: S&P 500 membership (premium on free tier,
    # degrades to [] -> membership simply left untouched).
    sp500 = set(client.index_constituents())
    _throttle(1)

    written = 0
    for symbol in symbols:
        try:
            profile = client.company_profile(symbol)
            metric = client.basic_financials(symbol)
            actuals = client.earnings_actuals(symbol)
            _throttle(3)

            if not profile and not metric and not actuals:
                logger.info(
                    "refresh_company_profiles: no data for %s; skipping", symbol
                )
                continue

            is_sp500 = (symbol in sp500) if sp500 else None
            values = _map_profile_row(symbol, profile, metric, actuals, is_sp500)
            await _upsert(
                session,
                CompanyFundamentals,
                values,
                "uq_company_fundamentals_symbol",
            )
            written += 1
        except SoftTimeLimitExceeded:  # soft time limit must wind the task down
            raise
        except Exception as exc:  # defensive: one bad symbol must not kill the run
            logger.error(
                "refresh_company_profiles: error for %s: %s", symbol, exc
            )

    await session.commit()
    logger.info("refresh_company_profiles: upserted %s of %s symbols", written, len(symbols))
    return written


async def _refresh_earnings_calendar(session, client) -> int:
    """Refresh earnings_calendar (future + recent) for watchlist equity symbols."""
    if not client.enabled:
        logger.warning(
            "refresh_earnings_calendar: Finnhub not configured; skipping cleanly"
        )
        return 0

    symbols = await _watchlist_stock_symbols(session)
    if not symbols:
        logger.info("refresh_earnings_calendar: no watchlist stock symbols; nothing to do")
        return 0

    today = date.today()
    start = (today - timedelta(days=CALENDAR_PAST_DAYS)).isoformat()
    end = (today + timedelta(days=CALENDAR_FUTURE_DAYS)).isoformat()

    written = 0
    for symbol in symbols:
        try:
            rows = client.earnings_calendar(start, end, symbol=symbol)
            _throttle(1)
            for row in rows:
                values = _map_calendar_row(row)
                if values is None:
                    continue
                await _upsert(
                    session,
                    EarningsCalendar,
                    values,
                    "uq_earnings_calendar_symbol_report_date",
                )
                written += 1
        except SoftTimeLimitExceeded:  # soft time limit must wind the task down
            raise
        except Exception as exc:
            logger.error(
                "refresh_earnings_calendar: error for %s: %s", symbol, exc
            )

    await session.commit()
    logger.info("refresh_earnings_calendar: upserted %s calendar rows", written)
    return written


async def _refresh_financials_on_earnings(session, client) -> int:
    """Fill `actual` figures for symbols that reported in the last few days.

    Scoped tightly: we only hit the API for a symbol that ALREADY has a recent
    calendar row in the DB, so a symbol that isn't reporting costs zero calls."""
    if not client.enabled:
        logger.warning(
            "refresh_financials_on_earnings: Finnhub not configured; skipping cleanly"
        )
        return 0

    symbols = await _watchlist_stock_symbols(session)
    if not symbols:
        logger.info(
            "refresh_financials_on_earnings: no watchlist stock symbols; nothing to do"
        )
        return 0

    today = date.today()
    start = today - timedelta(days=EARNINGS_LOOKBACK_DAYS)

    written = 0
    for symbol in symbols:
        try:
            recent_stmt = select(EarningsCalendar.id).where(
                EarningsCalendar.symbol == symbol,
                EarningsCalendar.report_date >= start,
                EarningsCalendar.report_date <= today,
            )
            recent = (await session.execute(recent_stmt)).scalars().all()
            if not recent:
                continue  # nothing reported recently for this symbol -> no API call

            rows = client.earnings_calendar(
                start.isoformat(), today.isoformat(), symbol=symbol
            )
            actuals = client.earnings_actuals(symbol)
            _throttle(2)

            wrote = False
            for row in rows:
                values = _map_calendar_row(row)
                if values is None:
                    continue
                if values.get("eps_actual") is None and values.get("rev_actual") is None:
                    continue  # still no actuals -> leave the scheduled row as-is
                await _upsert(
                    session,
                    EarningsCalendar,
                    values,
                    "uq_earnings_calendar_symbol_report_date",
                )
                wrote = True

            if isinstance(actuals, list) and actuals:
                await _upsert(
                    session,
                    CompanyFundamentals,
                    {"symbol": symbol, "historical_financials": actuals},
                    "uq_company_fundamentals_symbol",
                )
                wrote = True

            if wrote:
                written += 1
        except SoftTimeLimitExceeded:  # soft time limit must wind the task down
            raise
        except Exception as exc:
            logger.error(
                "refresh_financials_on_earnings: error for %s: %s", symbol, exc
            )

    await session.commit()
    logger.info(
        "refresh_financials_on_earnings: updated actuals for %s symbols", written
    )
    return written


# ---- Celery task wrappers -------------------------------------------------


def _run(core) -> None:
    """Drive a task core with a fresh session + client, never letting it crash."""
    async def _inner():
        async with CeleryAsyncSessionLocal() as session:
            client = get_finnhub_client()
            try:
                await core(session, client)
            except SoftTimeLimitExceeded:  # soft time limit must wind the task down
                raise
            except Exception as exc:  # last-resort guardrail
                logger.error("%s failed: %s", core.__name__, exc)
                await session.rollback()

    asyncio.run(_inner())


@shared_task(name="fundamentals.refresh_company_profiles")
def refresh_company_profiles():
    """Weekly: refresh slow-moving company profiles + historical financials."""
    _run(_refresh_company_profiles)


@shared_task(name="fundamentals.refresh_earnings_calendar")
def refresh_earnings_calendar():
    """Daily (pre-market): refresh the future + recent earnings calendar + estimates."""
    _run(_refresh_earnings_calendar)


@shared_task(name="fundamentals.refresh_financials_on_earnings")
def refresh_financials_on_earnings():
    """Daily: fill in actual EPS/revenue for symbols that just reported."""
    _run(_refresh_financials_on_earnings)
