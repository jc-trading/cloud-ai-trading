"""Price-claim outcomes (P7a): did the named stock beat SPY by the claimed move
between the anchor session's adjusted open and the evaluate_after session's
adjusted close. Pure functions; the caller supplies the bars. The sector ETF
move is a diagnostic only and never decides the hit, which is judged on the
unrounded excess; a miss scores 0, a hit the share of the claim realized."""

from __future__ import annotations

import math
from datetime import date

from app.modules.news import rules

SECTOR_ETF: dict[str, str] = {
    **dict.fromkeys(("Technology", "Semiconductors"), "XLK"),
    **dict.fromkeys(("Banking", "Financial Services", "Insurance"), "XLF"),
    **dict.fromkeys(("Biotechnology", "Health Care", "Pharmaceuticals",
                     "Life Sciences Tools & Services"), "XLV"),
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    **dict.fromkeys(("Retail", "Hotels, Restaurants & Leisure", "Diversified Consumer Services",
                     "Textiles, Apparel & Luxury Goods", "Auto Components", "Automobiles",
                     "Leisure Products", "Distributors"), "XLY"),
    **dict.fromkeys(("Media", "Telecommunication", "Communications"), "XLC"),
    **dict.fromkeys(("Food Products", "Beverages", "Tobacco", "Consumer products"), "XLP"),
    **dict.fromkeys(("Chemicals", "Metals & Mining", "Packaging", "Paper & Forest"), "XLB"),
    **dict.fromkeys(("Electrical Equipment", "Machinery", "Professional Services",
                     "Aerospace & Defense", "Commercial Services & Supplies", "Construction",
                     "Trading Companies & Distributors", "Road & Rail", "Building", "Marine",
                     "Airlines", "Logistics & Transportation", "Transportation Infrastructure",
                     "Industrial Conglomerates"), "XLI"),
}


def sector_etf(symbol: str, sector_map: dict[str, str]) -> str | None:
    return SECTOR_ETF.get(sector_map.get(symbol))


def daily_prices(df) -> dict[date, tuple[float, float]] | None:
    if df.attrs.get("unadjusted"):
        return None
    if df.empty:
        return {}
    sessions = df["ts"].dt.tz_convert("America/New_York").dt.date
    return {d: (float(o), float(c)) for d, o, c in zip(sessions, df["open"], df["close"])}


def _usable(prices: dict, anchor: date, end: date) -> bool:
    return all(math.isfinite(x) and x > 0 for x in (prices[anchor][0], prices[end][1]))


def _move(prices: dict, anchor: date, end: date) -> float:
    return (prices[end][1] / prices[anchor][0] - 1) * 100


def evaluate_price(*, direction: str, expected: float, anchor: date, end: date,
                   prices: dict | None, spy: dict | None, sector: dict | None = None,
                   etf: str | None = None) -> dict:
    if prices is None:
        return {"status": "no_data", "reason": "unadjusted"}
    if anchor not in prices:
        return {"status": "no_data", "reason": "no anchor bar"}
    if end not in prices:
        return {"status": "no_data", "reason": "no end bar"}
    if not isinstance(spy, dict) or anchor not in spy or end not in spy:
        return {"status": "no_data", "reason": "no SPY bar"}
    if not (_usable(prices, anchor, end) and _usable(spy, anchor, end)):
        return {"status": "no_data", "reason": "bad price"}

    realized = _move(prices, anchor, end)
    spy_move = _move(spy, anchor, end)
    excess = realized - spy_move
    thr = rules.hit_min_excess(expected)
    up = direction == "up"
    hit = excess >= thr if up else excess <= -thr
    signed = excess if up else -excess
    score = min(1.0, signed / expected) if hit else 0.0
    sector_move = (_move(sector, anchor, end)
                   if etf and sector and anchor in sector and end in sector
                   and _usable(sector, anchor, end) else None)
    return {
        "status": "scored",
        "reason": None,
        "anchor_price": round(prices[anchor][0], 6),
        "end_price": round(prices[end][1], 6),
        "realized_move_pct": round(realized, 4),
        "spy_move_pct": round(spy_move, 4),
        "excess_move_pct": round(excess, 4),
        "sector_etf": etf,
        "sector_move_pct": round(sector_move, 4) if sector_move is not None else None,
        "hit": hit,
        "score": round(score, 4),
        "magnitude_error_pct": round(signed - expected, 4),
    }
