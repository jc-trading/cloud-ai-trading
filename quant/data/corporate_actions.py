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
from quant.data import calendar

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
CREATE TABLE IF NOT EXISTS corporate_action_syncs (
    symbol     TEXT PRIMARY KEY,
    synced_at  TEXT NOT NULL          -- ISO date the provider was queried through
);
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.ACTIONS_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


# --- fetch (network) ------------------------------------------------------

def fetch_actions(symbols: list[str], start: date, end: date, *, client=None) -> list[dict]:
    """Fetch splits + cash dividends from Alpaca. Returns normalized dicts."""
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest

    if client is None:
        from dotenv import dotenv_values
        import os
        cfg = dotenv_values(str(config.REPO_ROOT / ".env"))
        client = CorporateActionsClient(
            cfg.get("ALPACA_API_KEY") or os.environ.get("ALPACA_API_KEY"),
            cfg.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_API_SECRET"))

    # limit=None: the SDK's CorporateActionsRequest defaults limit to 1000, which
    # silently truncates a multi-symbol 10y query to its first page — None lets
    # the client paginate until next_page_token runs out
    req = CorporateActionsRequest(symbols=[s.upper() for s in symbols],
                                  start=start, end=end, limit=None)
    resp = client.get_corporate_actions(req)
    data = resp.data if hasattr(resp, "data") else resp
    out: list[dict] = []

    def _get(obj, name):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    def _bucket(name):
        return data.get(name, []) if isinstance(data, dict) else getattr(data, name, []) or []

    # forward AND reverse splits share the new_rate/old_rate fields; ratio is
    # new/old in both directions (4:1 forward -> 4.0, 1:8 reverse -> 0.125),
    # which is exactly what adjust() expects (price_factor /= ratio).
    for s in list(_bucket("forward_splits")) + list(_bucket("reverse_splits")):
        new_rate = float(_get(s, "new_rate")); old_rate = float(_get(s, "old_rate"))
        out.append({
            "symbol": _get(s, "symbol"),
            "ex_date": _get(s, "ex_date"),
            "action_type": "split",
            "ratio": new_rate / old_rate if old_rate else None,
            "cash_amount": None,
        })
    # a stock dividend pays `rate` extra shares per share held — economically a
    # (1 + rate):1 split for adjustment purposes
    for s in _bucket("stock_dividends"):
        rate = _get(s, "rate")
        if rate is None:
            continue
        out.append({
            "symbol": _get(s, "symbol"),
            "ex_date": _get(s, "ex_date"),
            "action_type": "split",
            "ratio": 1.0 + float(rate),
            "cash_amount": None,
        })
    for d in _bucket("cash_dividends"):
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


def record_sync(symbols: list[str], synced_at: date, *,
                db_path: Path | None = None) -> int:
    """Watermark the last session the action feed is AUTHORITATIVE through.
    Without it 'this symbol has no corporate actions' is indistinguishable from
    'nobody ever asked', and the read-time RAW guard can never clear for a
    genuinely action-free symbol."""
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO corporate_action_syncs (symbol, synced_at) VALUES (?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET synced_at=excluded.synced_at",
            [(s.upper(), synced_at.isoformat()) for s in symbols])
        conn.commit()
    finally:
        conn.close()
    return len(symbols)


def load_synced_at(symbol: str, *, db_path: Path | None = None) -> date | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT synced_at FROM corporate_action_syncs WHERE symbol = ?",
            (symbol.upper(),)).fetchone()
    finally:
        conn.close()
    return date.fromisoformat(row[0]) if row else None


def sync_actions(symbols: list[str], start: date, end: date, *,
                 client=None, db_path: Path | None = None) -> int:
    n = store_actions(fetch_actions(symbols, start, end, client=client), db_path=db_path)
    # authoritative only THROUGH THE PREVIOUS session: signal_cycle syncs actions
    # before it fetches `end`'s bars, so that session's split may not be in the
    # feed yet. Stamping `end` would tell the RAW guard to skip the one bar that
    # can carry an unsynced split.
    record_sync(symbols, calendar.previous_session(end), db_path=db_path)
    return n


def count_symbols_with_actions(*, db_path: Path | None = None) -> int:
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM corporate_actions").fetchone()[0]
    finally:
        conn.close()


def sync_universe(symbols: list[str] | None = None, *, chunk_size: int = 100,
                  client=None, db_path: Path | None = None, progress=print) -> int:
    """Sync corporate actions for the FULL backfilled universe (point-in-time
    S&P500 union + ETF whitelist) over the daily-history window. Review finding
    B1: only symbols synced here get adjusted prices — a partial sync silently
    backtests the rest on RAW. Pass `symbols` to sync just tonight's set; the
    date window stays the full history because adjust() back-scales every bar
    before an ex-date, so a truncated action list mis-adjusts old bars."""
    from datetime import timedelta

    from quant.data import universe

    end = date.today()
    start = end - timedelta(days=365 * config.DAILY_HISTORY_YEARS + 7)
    if symbols is None:
        symbols = sorted(set(universe.all_symbols_in_range(start, end))
                         | set(config.ETF_WHITELIST))
    total = 0
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        total += sync_actions(chunk, start, end, client=client, db_path=db_path)
        progress(f"corporate actions: {min(i + chunk_size, len(symbols))}/{len(symbols)} "
                 f"symbols, {total} actions stored")
    return total


if __name__ == "__main__":
    n = sync_universe()
    print(f"done: {n} actions, {count_symbols_with_actions()} symbols have actions")


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
