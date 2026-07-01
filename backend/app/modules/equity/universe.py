"""Equity universe + daily candidate selection (EQUITY-universe-risk).

Decides WHICH US-equity symbols the research agent should look at on a given
day, cheaply and conservatively. Two ideas, straight from the SPEC:

  1. **Universe** = S&P 500 membership  ∩  a liquidity floor
     (last price >= $10  AND  average daily volume >= 1,000,000 shares).
     Membership + avg-volume are read from the ``company_fundamentals`` cache
     (filled by the fundamentals refresh tasks), NOT from a live Finnhub call —
     the free-tier quota is precious, so selection never hits the network.

  2. **Daily candidates** = the union of
       a. **earnings-driven names** — symbols that just REPORTED (actual EPS or
          revenue present) within the last few trading days (SPEC: "近 1-3 天出
          财报"), filtered down to the universe above; plus
       b. a **standing ~15-name watchlist** — a curated, always-analyzed set of
          large, liquid S&P 500 names (config below), so the agent has something
          to look at even on a quiet earnings day.

Design rules (mirror the scoring module's philosophy):
  * PURE where possible. ``liquidity_check`` and the recency helper are pure and
    unit-tested directly. The one DB-touching function (``select_daily_candidates``)
    takes an ``AsyncSession`` and an optional injectable ``price_lookup`` so a test
    can drive the whole flow with a fake session and no network.
  * FRUGAL. Exactly two DB reads (recent earnings + a batched fundamentals load)
    and ZERO Finnhub/Claude calls. Price is only consulted if a ``price_lookup`` is
    supplied; when it is not, the price leg is left *unverified* rather than
    forcing a per-symbol live quote.
  * CONSERVATIVE, but not self-defeating (documented ASSUMPTIONS):
      - An **earnings-driven** name must be a CONFIRMED universe member: cached
        ``is_sp500`` True AND avg-volume known and >= 1M. Missing data -> dropped
        ("default NO" for an unvetted dynamic name).
      - A **watchlist** name is a hand-curated S&P 500 mega-cap, so it is trusted
        as in-universe and is dropped ONLY on a *confirmed* liquidity failure
        (a known avg-volume < 1M or a known price < $10). Missing data keeps it.
      - The price floor is only *enforced* when a price is actually known; an
        unknown price is flagged (``price_verified=False``), never fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Awaitable, Callable, Optional, Union

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.equity.research import business_days_ago
from app.modules.fundamentals.models import CompanyFundamentals, EarningsCalendar

# --------------------------------------------------------------------------- #
# SPEC thresholds                                                              #
# --------------------------------------------------------------------------- #
LIQUIDITY_MIN_PRICE = 10.0            # last price >= $10
LIQUIDITY_MIN_AVG_VOLUME = 1_000_000.0  # average daily volume >= 1M shares

# "近 1-3 天出财报": a report counts as recent if it landed within this many
# TRADING days of today (0 = reported today). Kept in lock-step with the scoring
# module's recency gate (RECENCY_MAX_TRADING_DAYS = 3) so a name we select can
# still clear that gate downstream.
EARNINGS_RECENT_MAX_TRADING_DAYS = 3

# Standing ~15-name watchlist — large, liquid, unambiguously-S&P-500 names.
# Kept here in config (the task allows "config 或 watchlist 表标记"); a single
# edit here changes the always-on set without a migration.
DEFAULT_EQUITY_WATCHLIST: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "AVGO", "JPM", "V",
    "UNH", "XOM", "JNJ", "WMT", "PG",
)

# Injectable price source: symbol -> last price (or None). May be sync or async.
PriceLookup = Callable[[str], Union[Optional[float], Awaitable[Optional[float]]]]


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #
def _to_float(value) -> Optional[float]:
    """Best-effort float (handles Decimal / str / None). Never raises."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class LiquidityCheck:
    """Outcome of the two-part liquidity floor, keeping 'unknown' distinct from
    'fails'. ``passed`` requires a CONFIRMED volume >= 1M and no confirmed price
    breach; ``confirmed_fail`` means a known value is below its floor."""

    volume_known: bool
    volume_ok: bool
    price_known: bool
    price_ok: bool
    detail: str

    @property
    def price_verified(self) -> bool:
        return self.price_known

    @property
    def confirmed_fail(self) -> bool:
        return (self.volume_known and not self.volume_ok) or (
            self.price_known and not self.price_ok
        )

    @property
    def passed(self) -> bool:
        # Volume must be confirmed at/above the floor; price must not be a
        # confirmed breach (unknown price is tolerated but flagged).
        price_not_failing = (not self.price_known) or self.price_ok
        return self.volume_ok and price_not_failing

    def to_dict(self) -> dict:
        return {
            "volume_known": self.volume_known,
            "volume_ok": self.volume_ok,
            "price_known": self.price_known,
            "price_ok": self.price_ok,
            "passed": self.passed,
            "price_verified": self.price_verified,
            "detail": self.detail,
        }


def liquidity_check(
    price: Optional[float],
    avg_volume: Optional[float],
    *,
    min_price: float = LIQUIDITY_MIN_PRICE,
    min_avg_volume: float = LIQUIDITY_MIN_AVG_VOLUME,
) -> LiquidityCheck:
    """Evaluate the SPEC liquidity floor (price >= $10 AND avg_volume >= 1M).

    Missing inputs are reported as *unknown* rather than silently passing or
    failing, so the caller can decide how much trust to place in the datum.
    """
    volume_known = avg_volume is not None
    volume_ok = volume_known and avg_volume >= min_avg_volume
    price_known = price is not None
    price_ok = price_known and price >= min_price

    if not volume_known:
        vol_txt = "avg_volume unknown"
    elif volume_ok:
        vol_txt = f"avg_volume {avg_volume:,.0f} >= {min_avg_volume:,.0f} ok"
    else:
        vol_txt = f"avg_volume {avg_volume:,.0f} < {min_avg_volume:,.0f} FAIL"

    if not price_known:
        price_txt = "price unverified"
    elif price_ok:
        price_txt = f"price ${price:,.2f} >= ${min_price:,.0f} ok"
    else:
        price_txt = f"price ${price:,.2f} < ${min_price:,.0f} FAIL"

    return LiquidityCheck(
        volume_known=volume_known,
        volume_ok=volume_ok,
        price_known=price_known,
        price_ok=price_ok,
        detail=f"{vol_txt}; {price_txt}",
    )


def is_recent_report(
    report_date: Optional[date],
    *,
    today: Optional[date] = None,
    max_trading_days: int = EARNINGS_RECENT_MAX_TRADING_DAYS,
) -> tuple[bool, Optional[int]]:
    """Did a report land within ``max_trading_days`` trading days on/before today?

    Returns ``(is_recent, trading_days_ago)``. A future date (negative days ago)
    is NOT recent. ``today`` is injectable for deterministic tests.
    """
    days_ago = business_days_ago(report_date, today)
    if days_ago is None:
        return False, None
    is_recent = 0 <= days_ago <= max_trading_days
    return is_recent, days_ago


# --------------------------------------------------------------------------- #
# Candidate result containers                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    """One evaluated symbol + why it was (or wasn't) chosen — for the dashboard."""

    symbol: str
    sources: list[str]                       # "earnings" and/or "watchlist"
    selected: bool
    in_universe: bool
    report_date: Optional[date] = None
    earnings_days_ago: Optional[int] = None
    is_sp500: Optional[bool] = None
    avg_volume: Optional[float] = None
    price: Optional[float] = None
    liquidity: Optional[LiquidityCheck] = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "sources": list(self.sources),
            "selected": self.selected,
            "in_universe": self.in_universe,
            "report_date": self.report_date.isoformat() if self.report_date else None,
            "earnings_days_ago": self.earnings_days_ago,
            "is_sp500": self.is_sp500,
            "avg_volume": self.avg_volume,
            "price": self.price,
            "liquidity": self.liquidity.to_dict() if self.liquidity else None,
            "reasons": list(self.reasons),
        }


@dataclass
class DailyCandidates:
    """The day's selection outcome: chosen names + the rejected ones (audit)."""

    as_of: date
    selected: list[Candidate] = field(default_factory=list)
    rejected: list[Candidate] = field(default_factory=list)

    @property
    def symbols(self) -> list[str]:
        return [c.symbol for c in self.selected]

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "selected": [c.to_dict() for c in self.selected],
            "rejected": [c.to_dict() for c in self.rejected],
            "selected_symbols": self.symbols,
        }


# --------------------------------------------------------------------------- #
# DB reads (one for earnings, one batched for fundamentals) — testable          #
# --------------------------------------------------------------------------- #
async def _load_recent_reporters(
    db: AsyncSession, today: date, max_trading_days: int
) -> dict[str, tuple[date, int]]:
    """Map ``symbol -> (report_date, trading_days_ago)`` for names that REPORTED
    (actual EPS or revenue present) within ``max_trading_days`` trading days.

    A slightly wider CALENDAR window is used at the SQL layer (to bracket
    weekends/holidays), then narrowed precisely by trading-days in Python.
    """
    lower = today - timedelta(days=max_trading_days + 5)
    stmt = (
        select(EarningsCalendar)
        .where(
            EarningsCalendar.report_date >= lower,
            EarningsCalendar.report_date <= today,
            or_(
                EarningsCalendar.eps_actual.isnot(None),
                EarningsCalendar.rev_actual.isnot(None),
            ),
        )
        .order_by(EarningsCalendar.report_date.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    reporters: dict[str, tuple[date, int]] = {}
    for row in rows:
        symbol = (row.symbol or "").upper()
        if not symbol:
            continue
        recent, days_ago = is_recent_report(
            row.report_date, today=today, max_trading_days=max_trading_days
        )
        if not recent or days_ago is None:
            continue
        # Keep the most recent report per symbol (rows are date-desc).
        if symbol not in reporters:
            reporters[symbol] = (row.report_date, days_ago)
    return reporters


async def _load_fundamentals(
    db: AsyncSession, symbols: list[str]
) -> dict[str, CompanyFundamentals]:
    """Batched fundamentals load for the candidate universe (one query)."""
    if not symbols:
        return {}
    stmt = select(CompanyFundamentals).where(CompanyFundamentals.symbol.in_(symbols))
    rows = (await db.execute(stmt)).scalars().all()
    return {(r.symbol or "").upper(): r for r in rows}


async def _resolve_price(price_lookup: Optional[PriceLookup], symbol: str) -> Optional[float]:
    """Call an injected price source (sync or async); None on absence/any error."""
    if price_lookup is None:
        return None
    try:
        result = price_lookup(symbol)
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[assignment]
        return _to_float(result)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Main entry: build today's candidate pool                                     #
# --------------------------------------------------------------------------- #
async def select_daily_candidates(
    db: AsyncSession,
    *,
    today: Optional[date] = None,
    watchlist: Optional[list[str]] = None,
    max_trading_days_recent: int = EARNINGS_RECENT_MAX_TRADING_DAYS,
    price_lookup: Optional[PriceLookup] = None,
) -> DailyCandidates:
    """Build the day's equity candidate pool (earnings-driven ∪ standing watchlist).

    Pure w.r.t. the network: two DB reads, no Finnhub/Claude. ``price_lookup`` is
    optional and injectable; when omitted the price floor is left unverified (the
    liquidity gate then leans on the cached avg-volume, per the module docstring).
    """
    today = today or date.today()
    watch = [s.upper() for s in (watchlist if watchlist is not None else DEFAULT_EQUITY_WATCHLIST)]
    watch_set = set(watch)

    reporters = await _load_recent_reporters(db, today, max_trading_days_recent)

    # Union, preserving a stable order: watchlist first, then any extra reporters.
    ordered: list[str] = list(watch)
    for sym in reporters:
        if sym not in watch_set:
            ordered.append(sym)

    fundamentals = await _load_fundamentals(db, ordered)

    selected: list[Candidate] = []
    rejected: list[Candidate] = []

    for symbol in ordered:
        sources: list[str] = []
        if symbol in watch_set:
            sources.append("watchlist")
        if symbol in reporters:
            sources.append("earnings")

        report_date, days_ago = reporters.get(symbol, (None, None))
        fa = fundamentals.get(symbol)
        is_sp500 = bool(fa.is_sp500) if fa is not None and fa.is_sp500 is not None else None
        avg_volume = _to_float(getattr(fa, "avg_volume", None)) if fa is not None else None
        price = await _resolve_price(price_lookup, symbol)

        liq = liquidity_check(price, avg_volume)
        reasons: list[str] = [f"sources: {', '.join(sources)}", liq.detail]

        is_watchlist = "watchlist" in sources
        is_earnings = "earnings" in sources

        if is_watchlist:
            # Curated S&P 500 name: trusted as in-universe; drop only on a
            # CONFIRMED liquidity breach (known value below its floor).
            in_universe = True
            if is_sp500 is False:
                reasons.append("cached is_sp500=False, but watchlist is curated -> trusted in-universe")
            keep = not liq.confirmed_fail
            if keep:
                reasons.append("watchlist name -> selected (no confirmed liquidity breach)")
            else:
                reasons.append("watchlist name dropped -> confirmed liquidity breach")
        else:  # earnings-only, dynamic/unvetted -> demand confirmation
            in_universe = bool(is_sp500) and liq.volume_ok
            if is_sp500 is not True:
                reasons.append("not a CONFIRMED S&P 500 member in cache -> out of universe")
            if not liq.volume_ok:
                reasons.append("avg-volume not confirmed >= 1M -> liquidity not met")
            keep = in_universe and liq.passed
            if keep:
                reasons.append(
                    f"earnings {days_ago}d ago + confirmed universe/liquidity -> selected"
                )

        candidate = Candidate(
            symbol=symbol,
            sources=sources,
            selected=keep,
            in_universe=in_universe,
            report_date=report_date,
            earnings_days_ago=days_ago,
            is_sp500=is_sp500,
            avg_volume=avg_volume,
            price=price,
            liquidity=liq,
            reasons=reasons,
        )
        (selected if keep else rejected).append(candidate)

    return DailyCandidates(as_of=today, selected=selected, rejected=rejected)
