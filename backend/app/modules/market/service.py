"""
Market data service — US stocks only (Direction v3; crypto plane deleted).

Candles  → the local Parquet store via quant.data.bars.get_bars (方案 Phase 7);
           symbols the store does not cover fall back to Alpaca IEX REST, which
           is returned but never persisted (never mix an IEX file into the SIP
           store). Toggle with MARKET_CANDLES_FROM_STORE.
US Stocks → Alpaca Data API v2 (requires ALPACA_API_KEY in .env)
Quotes    → Finnhub real-time /quote overrides Alpaca's delayed IEX prices
Search    → Finnhub /search (full US universe) + curated stock list fallback
"""

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.exceptions import NotFoundException
from app.modules.fundamentals.finnhub_client import get_finnhub_client
from app.modules.market.models import MarketDataFile, MarketStreamSymbol

logger = logging.getLogger("cloud_ai_trading.market")


async def _override_stock_prices_with_finnhub(rows: list[dict]) -> None:
    """Alpaca's free tier serves the thin/laggy IEX feed, so its 'last' price and
    bid/ask drift from the real-time consolidated tape (what TradingView shows).
    Finnhub's free /quote is real-time and matches. Override each row's price in
    place from Finnhub; leave the row untouched if Finnhub has no quote. Best-effort,
    never raises. Concurrent so N symbols cost ~1 round-trip."""
    client = get_finnhub_client()
    if not client.enabled or not rows:
        return

    async def _q(sym: str):
        try:
            return sym, await asyncio.to_thread(client.quote, sym)
        except Exception:
            return sym, None

    quotes = dict(await asyncio.gather(*[_q(r["symbol"]) for r in rows]))
    for row in rows:
        q = quotes.get(row["symbol"])
        if not q:
            continue
        c = _f(q.get("c"))
        if c is None:
            continue
        pc = _f(q.get("pc"))
        row["last"] = c
        row["high"] = _f(q.get("h")) or row.get("high")
        row["low"] = _f(q.get("l")) or row.get("low")
        row["change_24h"] = round((c - pc) / pc * 100, 4) if pc else row.get("change_24h")
        # Drop the stale IEX bid/ask rather than show a misleading spread.
        row["bid"] = None
        row["ask"] = None
        if q.get("t"):
            row["timestamp"] = int(q["t"]) * 1000

# ── Default US stocks shown on market overview ──────────────────
DEFAULT_STOCK_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "AMD", "NFLX", "JPM",
    "V", "PLTR",
]

# ── Curated list of popular stocks for autocomplete search ───────
POPULAR_STOCKS: list[tuple[str, str]] = [
    # Big Tech
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corporation"),
    ("NVDA", "NVIDIA Corporation"),
    ("GOOGL", "Alphabet Inc. Class A"),
    ("GOOG", "Alphabet Inc. Class C"),
    ("AMZN", "Amazon.com Inc."),
    ("META", "Meta Platforms Inc."),
    ("TSLA", "Tesla Inc."),
    # Semiconductors
    ("AMD", "Advanced Micro Devices"),
    ("AVGO", "Broadcom Inc."),
    ("QCOM", "Qualcomm Inc."),
    ("INTC", "Intel Corporation"),
    ("MU", "Micron Technology"),
    ("AMAT", "Applied Materials"),
    ("LRCX", "Lam Research"),
    ("KLAC", "KLA Corporation"),
    ("TSM", "Taiwan Semiconductor"),
    ("ASML", "ASML Holding"),
    ("ARM", "Arm Holdings"),
    ("SMCI", "Super Micro Computer"),
    # Financials
    ("JPM", "JPMorgan Chase"),
    ("V", "Visa Inc."),
    ("MA", "Mastercard"),
    ("BAC", "Bank of America"),
    ("GS", "Goldman Sachs"),
    ("MS", "Morgan Stanley"),
    ("WFC", "Wells Fargo"),
    ("C", "Citigroup Inc."),
    ("AXP", "American Express"),
    ("BLK", "BlackRock Inc."),
    ("PYPL", "PayPal Holdings"),
    ("SQ", "Block Inc."),
    # Healthcare
    ("LLY", "Eli Lilly"),
    ("UNH", "UnitedHealth Group"),
    ("MRK", "Merck & Co."),
    ("ABBV", "AbbVie Inc."),
    ("TMO", "Thermo Fisher Scientific"),
    ("JNJ", "Johnson & Johnson"),
    ("PFE", "Pfizer Inc."),
    # Software / SaaS
    ("CRM", "Salesforce Inc."),
    ("ORCL", "Oracle Corporation"),
    ("ADBE", "Adobe Inc."),
    ("NOW", "ServiceNow"),
    ("WDAY", "Workday Inc."),
    ("SNOW", "Snowflake Inc."),
    ("PANW", "Palo Alto Networks"),
    ("CRWD", "CrowdStrike Holdings"),
    ("NET", "Cloudflare Inc."),
    ("DDOG", "Datadog Inc."),
    ("ZS", "Zscaler Inc."),
    ("MDB", "MongoDB Inc."),
    ("ZM", "Zoom Video"),
    ("DOCU", "DocuSign Inc."),
    ("OKTA", "Okta Inc."),
    ("TWLO", "Twilio Inc."),
    ("GTLB", "GitLab Inc."),
    # Consumer / Retail
    ("WMT", "Walmart Inc."),
    ("COST", "Costco Wholesale"),
    ("HD", "The Home Depot"),
    ("TGT", "Target Corporation"),
    ("NKE", "Nike Inc."),
    ("SBUX", "Starbucks Corporation"),
    ("MCD", "McDonald's Corporation"),
    # Media / Entertainment
    ("NFLX", "Netflix Inc."),
    ("DIS", "The Walt Disney Company"),
    ("SPOT", "Spotify Technology"),
    ("RBLX", "Roblox Corporation"),
    ("EA", "Electronic Arts"),
    ("TTWO", "Take-Two Interactive"),
    # Energy
    ("XOM", "Exxon Mobil"),
    ("CVX", "Chevron Corporation"),
    ("COP", "ConocoPhillips"),
    # Industrials
    ("BA", "The Boeing Company"),
    ("LMT", "Lockheed Martin"),
    ("CAT", "Caterpillar Inc."),
    ("GE", "GE Aerospace"),
    ("HON", "Honeywell International"),
    # Telecom
    ("T", "AT&T Inc."),
    ("VZ", "Verizon Communications"),
    ("CMCSA", "Comcast Corporation"),
    # Consumer Staples
    ("KO", "The Coca-Cola Company"),
    ("PEP", "PepsiCo Inc."),
    ("PG", "Procter & Gamble"),
    # Travel
    ("BKNG", "Booking Holdings"),
    ("ABNB", "Airbnb Inc."),
    ("UBER", "Uber Technologies"),
    ("LYFT", "Lyft Inc."),
    # Crypto / Fintech
    ("COIN", "Coinbase Global"),
    ("HOOD", "Robinhood Markets"),
    ("MSTR", "MicroStrategy"),
    ("PLTR", "Palantir Technologies"),
    # Crypto mining
    ("MARA", "Marathon Digital"),
    ("RIOT", "Riot Platforms"),
    # EV
    ("RIVN", "Rivian Automotive"),
    ("LCID", "Lucid Group"),
    ("NIO", "NIO Inc."),
    # AI / Infrastructure
    ("APP", "AppLovin Corporation"),
    ("ANET", "Arista Networks"),
    ("IBM", "IBM Corporation"),
    ("DELL", "Dell Technologies"),
    # Chinese ADRs
    ("BABA", "Alibaba Group"),
    ("JD", "JD.com Inc."),
    ("PDD", "PDD Holdings"),
    # Other popular
    ("GME", "GameStop Corp."),
    ("AMC", "AMC Entertainment"),
    ("F", "Ford Motor Company"),
    ("GM", "General Motors"),
    ("SHOP", "Shopify Inc."),
]

ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_TRADING_URL = "https://api.alpaca.markets"
ALPACA_INTERVAL_MAP = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min",
    "1h": "1Hour", "1d": "1Day",
}

# ── Local Parquet store (quant) ──────────────────────────────────
# Chart interval -> the timeframe vocabulary get_bars() speaks. 5m/15m are
# resampled from 1min inside get_bars; only daily may be adjusted, intraday is
# served RAW (get_bars raises on any other adjust for an intraday timeframe).
STORE_INTERVAL_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d"}
# regular-session bars per trading day — how the read window is sized
_SESSION_BARS = {"1m": 390, "5m": 78, "15m": 26, "1h": 7, "1d": 1}
_WINDOW_PAD_DAYS = 5  # holidays inside the window

# ── Shared HTTP clients ──────────────────────────────────────────
# These singletons are bound to the event loop they were created on. In FastAPI
# that is one long-lived loop, but Celery tasks create a FRESH loop per task
# (tasks/*.py _run_async). Reusing a client whose connections belong to a closed
# loop hangs forever — even its timeout timers live on the dead loop — which is
# exactly how the worker froze on 2026-07-01. So each accessor remembers its
# owning loop and rebuilds the client when called from a different one. The old
# client is dropped WITHOUT awaiting close(): closing over a dead loop can hang,
# and the closed loop's transports are already gone.
_alpaca_data_client: Optional[httpx.AsyncClient] = None
_alpaca_data_client_loop: Optional[asyncio.AbstractEventLoop] = None
_alpaca_api_key: str = ""
_alpaca_api_secret: str = ""


def _get_alpaca_client() -> Optional[httpx.AsyncClient]:
    """Return Alpaca Data API client (data.alpaca.markets)."""
    global _alpaca_data_client, _alpaca_data_client_loop
    global _alpaca_api_key, _alpaca_api_secret
    if not _alpaca_api_key:
        try:
            from app.config import get_settings
            s = get_settings()
            _alpaca_api_key = s.ALPACA_API_KEY
            _alpaca_api_secret = s.ALPACA_API_SECRET
        except Exception:
            return None

    if not _alpaca_api_key:
        return None

    loop = asyncio.get_running_loop()
    if (
        _alpaca_data_client is None
        or _alpaca_data_client.is_closed
        or _alpaca_data_client_loop is not loop
    ):
        _alpaca_data_client = httpx.AsyncClient(
            timeout=15.0,
            headers={
                "APCA-API-KEY-ID": _alpaca_api_key,
                "APCA-API-SECRET-KEY": _alpaca_api_secret,
                "Accept": "application/json",
            },
        )
        _alpaca_data_client_loop = loop
    return _alpaca_data_client


# ── Candle window + store reads ──────────────────────────────────

def _candle_window(interval: str, limit: int,
                   end: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Turn `limit` bars into the calendar window that certainly contains them.

    Only ~5 of every 7 calendar days trade and holidays eat a few more, so the
    window is deliberately wider than the arithmetic minimum; the caller takes
    the tail `limit` rows afterwards.
    """
    end = end or datetime.now(timezone.utc)
    sessions = math.ceil(limit / _SESSION_BARS[interval])
    days = math.ceil(sessions * 7 / 5) + _WINDOW_PAD_DAYS
    return end - timedelta(days=days), end


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bar_rows(df, limit: int) -> list[dict]:
    rows = []
    for r in df.tail(limit).itertuples(index=False):
        rows.append({
            "timestamp": int(r.ts.timestamp() * 1000),
            "open": _f(r.open) or 0.0,
            "high": _f(r.high) or 0.0,
            "low": _f(r.low) or 0.0,
            "close": _f(r.close) or 0.0,
            "volume": _f(r.volume) or 0.0,
        })
    return rows


def _read_store_candles(symbol: str, interval: str, start: datetime,
                        end: datetime, limit: int) -> list[dict]:
    """Chart candles out of the Parquet store. Returns [] when the symbol/timeframe
    is not covered (~700 symbols have daily only) so the caller can fall back."""
    from quant.data.bars import get_bars

    timeframe = STORE_INTERVAL_MAP[interval]
    try:
        df = get_bars(symbol.upper(), timeframe, start=start, end=end,
                      adjust="split_div" if timeframe == "1d" else "none")
    except Exception as e:
        logger.warning(f"store candles failed for {symbol} {interval}: {e}")
        return []
    return _bar_rows(df, limit) if not df.empty else []


def _read_store_bars(symbol: str, timeframe: str, start, end, limit: int) -> list[dict]:
    """Raw stored bars for the read-only /bars endpoint — no adjustment and no
    session filter, so what comes back is exactly what the files hold."""
    from quant.data.bars import get_bars

    try:
        df = get_bars(symbol.upper(), timeframe, start=start, end=end,
                      adjust="none", session="all")
    except Exception as e:
        logger.warning(f"store bars failed for {symbol} {timeframe}: {e}")
        return []
    if df.empty:
        return []
    return [
        {
            "t": r.ts.to_pydatetime(),
            "o": _f(r.open) or 0.0,
            "h": _f(r.high) or 0.0,
            "l": _f(r.low) or 0.0,
            "c": _f(r.close) or 0.0,
            "v": _f(r.volume) or 0.0,
            "vwap": _f(r.vwap),
            "n": int(r.trade_count) if _f(r.trade_count) else None,
        }
        for r in df.tail(limit).itertuples(index=False)
    ]


async def _alpaca_rest_candles(symbol: str, interval: str, start: datetime,
                               end: datetime, limit: int) -> list[dict]:
    """Alpaca IEX fallback for symbols the store does not cover. `start` is always
    sent — without it Alpaca answers with today's bars only."""
    client = _get_alpaca_client()
    if not client:
        return []
    try:
        resp = await client.get(
            f"{ALPACA_DATA_URL}/v2/stocks/bars",
            params={
                "symbols": symbol,
                "timeframe": ALPACA_INTERVAL_MAP[interval],
                "start": _rfc3339(start),
                "end": _rfc3339(end),
                "limit": limit,
                "feed": "iex",
                "sort": "desc",
            },
        )
        resp.raise_for_status()
        bars = resp.json().get("bars", {}).get(symbol, [])
        bars = sorted(bars, key=lambda b: b["t"])[-limit:]
        return [
            {
                "timestamp": int(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp() * 1000),
                "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"],
            }
            for b in bars
        ]
    except Exception as e:
        logger.error(f"Alpaca stock candles failed for {symbol}: {e}")
        return []


# ── Popular stocks index for fast search (deduped by symbol) ─────
_seen: set = set()
_STOCKS_INDEX: list[dict] = []
for _s, _n in POPULAR_STOCKS:
    if _s not in _seen:
        _seen.add(_s)
        _STOCKS_INDEX.append({"symbol": _s, "name": _n})
del _seen, _s, _n


class MarketService:

    @staticmethod
    async def get_ticker(symbol: str) -> dict:
        tickers = await MarketService.get_stock_tickers([symbol])
        if tickers:
            return tickers[0]
        raise ValueError(f"Stock not found: {symbol}")

    @staticmethod
    async def get_candles(symbol: str, interval: str = "1h", limit: int = 100) -> list[dict]:
        """Stocks-only alias kept for parked modules (analysis) that still call it."""
        return await MarketService.get_stock_candles(symbol, interval, limit)

    # ── US Stocks (Alpaca) ────────────────────────────────────────

    @staticmethod
    async def get_stock_tickers(symbols: Optional[list[str]] = None) -> list[dict]:
        target = symbols if symbols else DEFAULT_STOCK_SYMBOLS
        client = _get_alpaca_client()
        if not client:
            logger.warning("Alpaca API keys not configured")
            return []
        try:
            resp = await client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/snapshots",
                params={"symbols": ",".join(target), "feed": "iex"},
            )
            resp.raise_for_status()
            data = resp.json()
            rows = [
                _format_alpaca_snapshot(sym, data[sym])
                for sym in target if sym in data
            ]
            # Real-time price from Finnhub (Alpaca free tier is delayed IEX).
            await _override_stock_prices_with_finnhub(rows)
            return rows
        except Exception as e:
            logger.error(f"Alpaca stock tickers failed: {e}")
            return []

    @staticmethod
    async def get_stock_candles(symbol: str, interval: str = "1h", limit: int = 100) -> list[dict]:
        if interval not in STORE_INTERVAL_MAP:
            interval = "1h"
        start, end = _candle_window(interval, limit)
        if get_settings().MARKET_CANDLES_FROM_STORE:
            rows = await asyncio.to_thread(
                _read_store_candles, symbol, interval, start, end, limit)
            if rows:
                return rows
        return await _alpaca_rest_candles(symbol, interval, start, end, limit)

    # ── Local store: raw bars + subscription set ──────────────────

    @staticmethod
    async def get_bars(db: AsyncSession, symbol: str, timeframe: str, start=None,
                       end=None, limit: int = 1000) -> dict:
        """Read-only bars envelope: the stored rows plus the provenance of the
        newest registered file for that (symbol, timeframe)."""
        rows = await asyncio.to_thread(
            _read_store_bars, symbol, timeframe, start, end, limit)
        meta = (await db.execute(
            select(MarketDataFile.provider, MarketDataFile.last_ts)
            .where(MarketDataFile.symbol == symbol.upper(),
                   MarketDataFile.timeframe == timeframe)
            .order_by(MarketDataFile.last_ts.desc())
            .limit(1)
        )).first()
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "provider": meta[0] if meta else None,
            "last_ts": meta[1] if meta else None,
            "bars": rows,
        }

    @staticmethod
    async def list_stream_symbols(db: AsyncSession) -> list[MarketStreamSymbol]:
        result = await db.execute(
            select(MarketStreamSymbol)
            .order_by(MarketStreamSymbol.priority.asc(), MarketStreamSymbol.symbol.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def upsert_stream_symbol(db: AsyncSession, symbol: str, priority: int,
                                   note: Optional[str]) -> MarketStreamSymbol:
        sym = symbol.strip().upper()
        result = await db.execute(
            select(MarketStreamSymbol).where(MarketStreamSymbol.symbol == sym)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = MarketStreamSymbol(symbol=sym, priority=priority, note=note)
            db.add(row)
        else:
            row.priority = priority
            row.note = note
        row.enabled = True
        await db.flush()
        await db.refresh(row)
        return row

    @staticmethod
    async def disable_stream_symbol(db: AsyncSession, symbol: str) -> MarketStreamSymbol:
        result = await db.execute(
            select(MarketStreamSymbol).where(MarketStreamSymbol.symbol == symbol.strip().upper())
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundException(f"Stream symbol {symbol.strip().upper()}")
        row.enabled = False
        await db.flush()
        await db.refresh(row)
        return row

    # ── Search (autocomplete) ─────────────────────────────────────

    @staticmethod
    async def search_stock_suggestions(query: str, limit: int = 8) -> list[dict]:
        """Search popular stocks by symbol or name, enriched with live prices."""
        q = query.strip().upper()
        if not q:
            return []

        # Full-universe search via Finnhub /search (matches ticker + company
        # name/description across ALL US-listed symbols). Falls back to the
        # built-in popular-stocks index when Finnhub is unavailable.
        matched: list[dict] = []
        _fc = get_finnhub_client()
        if _fc.enabled:
            try:
                _raw = await asyncio.to_thread(_fc.symbol_search, q)
            except Exception:
                _raw = []
            _seen: set[str] = set()
            _us: list[dict] = []
            for _r in _raw:
                _sym = (_r.get("symbol") or "").upper()
                _typ = _r.get("type") or ""
                if not _sym or _sym in _seen:
                    continue
                if "." in _sym or ":" in _sym:  # skip non-US / exchange-suffixed listings
                    continue
                if _typ and _typ not in ("Common Stock", "ETP", "ETF", "ADR"):
                    continue
                _seen.add(_sym)
                _us.append({"symbol": _sym, "name": _r.get("description") or ""})
            _exact  = [s for s in _us if s["symbol"] == q]
            _starts = [s for s in _us if s["symbol"].startswith(q) and s["symbol"] != q]
            _rest   = [s for s in _us if s not in _exact and s not in _starts]
            matched = (_exact + _starts + _rest)[:limit]

        if not matched:  # Finnhub off/empty → built-in popular-stocks index
            exact   = [s for s in _STOCKS_INDEX if s["symbol"] == q]
            starts  = [s for s in _STOCKS_INDEX if s["symbol"].startswith(q) and s["symbol"] != q]
            mid_sym = [s for s in _STOCKS_INDEX if q in s["symbol"] and not s["symbol"].startswith(q)]
            name_m  = [s for s in _STOCKS_INDEX if q in s["name"].upper() and q not in s["symbol"]]
            matched = (exact + starts + mid_sym + name_m)[:limit]

        if not matched:
            return [{"symbol": q, "name": "", "last": None, "change_24h": None, "market_type": "stock"}]

        # Fetch live prices for matched symbols
        syms = [m["symbol"] for m in matched]
        price_map: dict[str, dict] = {}
        client = _get_alpaca_client()
        if client:
            try:
                resp = await client.get(
                    f"{ALPACA_DATA_URL}/v2/stocks/snapshots",
                    params={"symbols": ",".join(syms), "feed": "iex"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for sym in syms:
                        if sym in data:
                            price_map[sym] = _format_alpaca_snapshot(sym, data[sym])
            except Exception as e:
                logger.warning(f"Price enrichment failed in search: {e}")

        results = []
        for m in matched:
            p = price_map.get(m["symbol"], {})
            last = p.get("last") or None  # treat 0 as None for display
            change_24h = p.get("change_24h")
            # Compute dollar change from prev_close implied by last + change%
            change_dollar = None
            if last and change_24h is not None:
                prev_close = last / (1 + change_24h / 100) if change_24h != -100 else None
                if prev_close:
                    change_dollar = round(last - prev_close, 4)
            results.append({
                "symbol": m["symbol"],
                "name": m["name"],
                "last": last,
                "change_24h": change_24h,
                "change_dollar": change_dollar,
                "market_type": "stock",
            })
        return results


# ── Formatters ────────────────────────────────────────────────────

def _format_alpaca_snapshot(symbol: str, snap: dict) -> dict:
    """
    Parse Alpaca snapshot response.
    Handles weekends / after-hours where dailyBar may be null:
    - latestTrade.p  → most recent executed trade price
    - minuteBar.c    → last 1-min bar close (after-hours / pre-market)
    - dailyBar.c     → today's session close (null on weekends)
    - prevDailyBar.c → Friday's close (primary fallback on weekends)
    """
    daily_bar    = snap.get("dailyBar") or {}
    prev_bar     = snap.get("prevDailyBar") or {}
    latest_trade = snap.get("latestTrade") or {}
    latest_quote = snap.get("latestQuote") or {}
    minute_bar   = snap.get("minuteBar") or {}

    # Price: waterfall through available sources
    last_price = (
        _f(latest_trade.get("p"))
        or _f(minute_bar.get("c"))
        or _f(daily_bar.get("c"))
        or _f(prev_bar.get("c"))
    )

    # High / Low: today's session, fallback to previous day
    high   = _f(daily_bar.get("h")) or _f(prev_bar.get("h"))
    low    = _f(daily_bar.get("l")) or _f(prev_bar.get("l"))
    volume = _f(daily_bar.get("v")) or _f(prev_bar.get("v"))

    # 24h change relative to previous day's close
    prev_close = _f(prev_bar.get("c"))
    change_24h = (
        round((last_price - prev_close) / prev_close * 100, 4)
        if prev_close and last_price
        else None
    )

    return {
        "symbol":       symbol,
        "last":         last_price or 0,
        "bid":          _f(latest_quote.get("bp")),
        "ask":          _f(latest_quote.get("ap")),
        "high":         high or 0,
        "low":          low or 0,
        "volume":       volume or 0,
        "quote_volume": None,
        "change_24h":   change_24h,
        "timestamp":    None,
        "market_type":  "stock",
    }


def _f(val) -> Optional[float]:
    """Safe float conversion — returns None (not 0) if missing/zero."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f != 0.0 else None
    except (TypeError, ValueError):
        return None
