"""
Market data service — US stocks only (Direction v3; crypto plane deleted).

US Stocks → Alpaca Data API v2 (requires ALPACA_API_KEY in .env)
Quotes    → Finnhub real-time /quote overrides Alpaca's delayed IEX prices
Search    → Finnhub /search (full US universe) + curated stock list fallback
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from app.modules.fundamentals.finnhub_client import get_finnhub_client

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
    "1h": "1Hour", "4h": "4Hour", "1d": "1Day",
}

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
        client = _get_alpaca_client()
        if not client:
            return []
        timeframe = ALPACA_INTERVAL_MAP.get(interval, "1Hour")
        try:
            resp = await client.get(
                f"{ALPACA_DATA_URL}/v2/stocks/bars",
                params={"symbols": symbol, "timeframe": timeframe, "limit": limit, "feed": "iex", "sort": "desc"},
            )
            resp.raise_for_status()
            bars = resp.json().get("bars", {}).get(symbol, [])
            bars = sorted(bars, key=lambda b: b["t"])
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
