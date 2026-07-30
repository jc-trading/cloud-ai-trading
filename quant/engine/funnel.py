"""Screening funnel — generator + filter chain (design §7; A4/A8).

Pure, composable, each layer explainable: given a per-symbol feature frame, apply
liquidity -> volatility -> trend-alignment filters, sort by confidence, enforce a
per-sector cap, and take the top N. Stocks only — ETFs use a separate whitelist
quota (A4), never scored in the same pool (sector cap is meaningless for SPY/QQQ,
and holding a broad ETF next to single names double-counts beta).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant import config

# expected feature columns: symbol, price, adv, atr_pct, above_rising_ma20,
# sector, confidence, direction


@dataclass(frozen=True)
class FunnelParams:
    min_adv: float = config.MIN_AVG_DOLLAR_VOLUME
    min_price: float = config.MIN_PRICE
    atr_pct_min: float = 0.01      # [A8/C] too calm = no profit
    atr_pct_max: float = 0.08      # [A8/C] too wild = stops get swept
    max_per_sector: int = config.MAX_PER_SECTOR
    shortlist_max: int = config.SHORTLIST_MAX
    min_confidence: float = config.MIN_CONFIDENCE  # [C1] entry threshold knob
                                                   # (review B4: rank-only had
                                                   # nothing to calibrate)


def filter_liquidity(df: pd.DataFrame, p: FunnelParams) -> pd.DataFrame:
    return df[(df["adv"] > p.min_adv) & (df["price"] > p.min_price)]


def filter_volatility(df: pd.DataFrame, p: FunnelParams) -> pd.DataFrame:
    return df[(df["atr_pct"] >= p.atr_pct_min) & (df["atr_pct"] <= p.atr_pct_max)]


def filter_trend_alignment(df: pd.DataFrame) -> pd.DataFrame:
    # long-only [A1]: only names in a confirmed uptrend (price above a rising MA20)
    return df[df["above_rising_ma20"] & (df["direction"] == "up")]


def apply_sector_cap(df: pd.DataFrame, max_per_sector: int) -> pd.DataFrame:
    """Keep at most N per sector, preferring higher confidence (df must be sorted
    by confidence desc). Hard cap — 3 slots all in semis = one bet decides fate."""
    return df.groupby("sector", sort=False).head(max_per_sector)


def build_shortlist(features: pd.DataFrame, params: FunnelParams = FunnelParams()) -> list[str]:
    """Full chain -> ordered list of <= shortlist_max symbols. STOCKS only —
    whitelisted ETFs are excluded up front (A4-Extra: they have their own quota
    via select_etfs and must never be scored in the stock pool)."""
    if features.empty:
        return []
    df = features[~features["symbol"].isin(config.ETF_WHITELIST)]
    df = filter_liquidity(df, params)
    df = filter_volatility(df, params)
    df = filter_trend_alignment(df)
    df = df[df["confidence"] >= params.min_confidence]   # [C1]
    if df.empty:
        return []
    df = df.sort_values("confidence", ascending=False)
    df = apply_sector_cap(df, params.max_per_sector)
    return df.head(params.shortlist_max)["symbol"].tolist()


def select_etfs(features: pd.DataFrame, *, max_slots: int = config.ETF_MAX_SLOTS,
                whitelist: tuple[str, ...] = config.ETF_WHITELIST) -> list[str]:
    """ETF quota (A4): only whitelisted ETFs in a confirmed uptrend, capped at
    max_slots, ranked by confidence. Separate from the stock funnel."""
    if features.empty:
        return []
    df = features[features["symbol"].isin(whitelist)]
    df = df[df["above_rising_ma20"] & (df["direction"] == "up")]
    df = df.sort_values("confidence", ascending=False)
    return df.head(max_slots)["symbol"].tolist()
