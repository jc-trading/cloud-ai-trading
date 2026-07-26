"""Corporate actions (splits + cash dividends) and read-time price adjustment.

Design §4.1: bars are STORED raw and adjusted on READ, so a split never rewrites
frozen history. This module fetches actions (Alpaca primary, design D4), caches
them (SQLite in R0 -> PostgreSQL in R1), and applies CRSP-style back-adjustment:
the most recent prices stay real, and historical prices are scaled so the series
is continuous across split/dividend dates (no fake gaps -> no fake crossovers).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from quant import config

_SOURCE = "alpaca"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol       TEXT NOT NULL,
    ex_date      TEXT NOT NULL,           -- ISO date
    action_type  TEXT NOT NULL,           -- 'split' | 'dividend'
    ratio        REAL,                    -- split: new_rate/old_rate; else NULL
    cash_amount  REAL,                    -- dividend $/share; else NULL
    source       TEXT NOT NULL,
    UNIQUE(symbol, ex_date, action_type)
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.MANIFEST_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(_SCHEMA)
    return conn


# --- fetch (network) ------------------------------------------------------

def fetch_actions(symbols: list[str], start: date, end: date, *, client=None) -> list[dict]:
    """Fetch splits + cash dividends from Alpaca. Returns normalized dicts."""
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest

    if client is None:
        from dotenv import dotenv_values
        cfg = dotenv_values(str(config.REPO_ROOT / ".env"))
        client = CorporateActionsClient(cfg["ALPACA_API_KEY"], cfg["ALPACA_API_SECRET"])

    req = CorporateActionsRequest(symbols=[s.upper() for s in symbols], start=start, end=end)
    resp = client.get_corporate_actions(req)
    data = resp.data if hasattr(resp, "data") else resp
    out: list[dict] = []

    def _get(obj, name):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    for s in data.get("forward_splits", []) if isinstance(data, dict) else getattr(data, "forward_splits", []):
        new_rate = float(_get(s, "new_rate")); old_rate = float(_get(s, "old_rate"))
        out.append({
            "symbol": _get(s, "symbol"),
            "ex_date": _get(s, "ex_date"),
            "action_type": "split",
            "ratio": new_rate / old_rate if old_rate else None,
            "cash_amount": None,
        })
    divs = data.get("cash_dividends", []) if isinstance(data, dict) else getattr(data, "cash_dividends", [])
    for d in divs:
        out.append({
            "symbol": _get(d, "symbol"),
            "ex_date": _get(d, "ex_date"),
            "action_type": "dividend",
            "ratio": None,
            "cash_amount": float(_get(d, "rate")) if _get(d, "rate") is not None else None,
        })
    return out


def store_actions(actions: list[dict], *, db_path: Path | None = None) -> int:
    conn = _connect(db_path)
    n = 0
    try:
        for a in actions:
            ex = a["ex_date"]
            ex_iso = ex.isoformat() if isinstance(ex, (date, datetime)) else str(ex)
            conn.execute(
                "INSERT INTO corporate_actions (symbol, ex_date, action_type, ratio, "
                "cash_amount, source) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(symbol, ex_date, action_type) DO UPDATE SET "
                "ratio=excluded.ratio, cash_amount=excluded.cash_amount, source=excluded.source",
                (a["symbol"], ex_iso, a["action_type"], a["ratio"], a["cash_amount"], _SOURCE),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def load_actions(symbol: str, *, db_path: Path | None = None) -> pd.DataFrame:
    conn = _connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT symbol, ex_date, action_type, ratio, cash_amount FROM "
            "corporate_actions WHERE symbol = ? ORDER BY ex_date",
            conn, params=(symbol.upper(),),
        )
    finally:
        conn.close()
    if not df.empty:
        df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
    return df


def sync_actions(symbols: list[str], start: date, end: date, *,
                 client=None, db_path: Path | None = None) -> int:
    return store_actions(fetch_actions(symbols, start, end, client=client), db_path=db_path)


# --- read-time adjustment (pure) ------------------------------------------

_PRICE_COLS = ("open", "high", "low", "close", "vwap")


def adjust(df_raw: pd.DataFrame, actions: pd.DataFrame, *, mode: str = "split_div") -> pd.DataFrame:
    """Back-adjust a RAW bar frame for splits (and dividends if mode=='split_div').

    A bar dated < ex_date is scaled by the cumulative factor of every later
    action; bars on/after every ex_date are unchanged (real current prices).
    Split: prices *= 1/ratio, volume *= ratio (dollar volume preserved).
    Dividend: prices *= (1 - amount/close_before_ex).
    """
    if df_raw.empty or actions is None or actions.empty or mode == "none":
        return df_raw.copy()

    df = df_raw.copy().sort_values("ts").reset_index(drop=True)
    bar_date = df["ts"].dt.tz_convert("America/New_York").dt.date
    price_factor = pd.Series(1.0, index=df.index)
    volume_factor = pd.Series(1.0, index=df.index)

    for _, act in actions.sort_values("ex_date").iterrows():
        ex = act["ex_date"]
        before = bar_date < ex
        if act["action_type"] == "split" and act["ratio"]:
            price_factor[before] /= float(act["ratio"])
            volume_factor[before] *= float(act["ratio"])
        elif act["action_type"] == "dividend" and mode == "split_div" and act["cash_amount"]:
            # close on the last bar strictly before ex_date (raw)
            prior = df.loc[before, "close"]
            if prior.empty:
                continue
            close_before = float(prior.iloc[-1])
            if close_before > 0:
                price_factor[before] *= (1.0 - float(act["cash_amount"]) / close_before)

    for col in _PRICE_COLS:
        if col in df.columns:
            df[col] = df[col] * price_factor
    if "volume" in df.columns:
        df["volume"] = df["volume"] * volume_factor
    return df
