"""Sector cache (review B3, second half): one GICS-ish sector per symbol.

Without real sectors every name is 'unknown' and the funnel's per-sector cap
(A8, <=2) silently collapses the whole shortlist into a single 2-name bucket —
the reason the R0-7 smoke ran with the cap off. Source: Finnhub company
profile2 `finnhubIndustry` (free tier, ~60 req/min). Cached to
cat-data/universe/sectors.parquet; the sync is resumable (already-cached
symbols are skipped) and delisted symbols degrade to 'unknown' LOUDLY in the
summary, never silently.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

import pandas as pd

from quant import config

SECTORS_PARQUET = config.UNIVERSE_DIR / "sectors.parquet"
_PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2?symbol={symbol}&token={token}"


def load_sectors() -> dict[str, str]:
    """symbol -> sector ('unknown' for names Finnhub has no profile for)."""
    if not SECTORS_PARQUET.exists():
        return {}
    df = pd.read_parquet(SECTORS_PARQUET)
    return dict(zip(df["symbol"], df["sector"]))


def _save(sectors: dict[str, str]) -> None:
    config.UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(sorted(sectors.items()), columns=["symbol", "sector"]).to_parquet(
        SECTORS_PARQUET, index=False)


def fetch_sector(symbol: str, token: str, *, timeout: int = 15) -> str | None:
    url = _PROFILE_URL.format(symbol=symbol, token=token)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.load(resp)
    return (data or {}).get("finnhubIndustry") or None


def sync_sectors(symbols: list[str] | None = None, *, delay_s: float = 1.05,
                 progress=print) -> dict[str, str]:
    """Fetch + cache sectors for the universe (resumable, rate-limited)."""
    from dotenv import dotenv_values

    token = dotenv_values(str(config.REPO_ROOT / ".env")).get("FINNHUB_API_KEY")
    if not token:
        raise RuntimeError("FINNHUB_API_KEY missing from .env")
    if symbols is None:
        from quant.data import universe
        end = date.today()
        start = end - timedelta(days=365 * config.DAILY_HISTORY_YEARS + 7)
        symbols = sorted(set(universe.all_symbols_in_range(start, end))
                         | set(config.ETF_WHITELIST))

    sectors = load_sectors()
    todo = [s for s in symbols if s not in sectors]
    progress(f"sectors: {len(sectors)} cached, {len(todo)} to fetch")
    for i, sym in enumerate(todo, 1):
        try:
            sec = fetch_sector(sym, token)
        except urllib.error.HTTPError as e:
            if e.code == 429:                      # free-tier burst limit
                time.sleep(30)
                try:
                    sec = fetch_sector(sym, token)
                except Exception:
                    sec = None
            else:
                sec = None
        except Exception:
            sec = None
        sectors[sym] = sec or "unknown"
        if i % 50 == 0 or i == len(todo):
            _save(sectors)                         # resumable checkpoints
            progress(f"sectors: {i}/{len(todo)} fetched")
        time.sleep(delay_s)
    _save(sectors)
    unknown = sorted(s for s, sec in sectors.items() if sec == "unknown")
    progress(f"sectors done: {len(sectors)} total, {len(unknown)} unknown"
             + (f" ({', '.join(unknown[:15])}{'...' if len(unknown) > 15 else ''})"
                if unknown else ""))
    return sectors


if __name__ == "__main__":
    sync_sectors()
