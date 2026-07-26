"""R0-3 corporate-actions tests: split/dividend back-adjustment (no network) +
SQLite store roundtrip."""

from datetime import date

import pandas as pd

from quant.data import corporate_actions as ca


def _daily(dates, closes, vols=None):
    # Anchor at true ET midnight (04:00Z in EDT, 05:00Z in EST) exactly like
    # Alpaca's daily bars, so tz_convert back to ET recovers the right date
    # year-round.
    n = len(dates)
    vols = vols or [1_000_000] * n
    ts = pd.DatetimeIndex(
        [pd.Timestamp(f"{d} 00:00", tz="America/New_York") for d in dates]
    ).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts,
        "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
        "close": closes, "volume": vols, "vwap": closes,
        "trade_count": [100] * n,
    })


def test_split_back_adjust_no_fake_jump():
    # 4:1 split at ex_date 2020-08-31: raw drops 400 -> 100 across the date
    df = _daily(
        ["2020-08-27", "2020-08-28", "2020-08-31", "2020-09-01"],
        [400.0, 404.0, 101.0, 102.0],
        vols=[1_000_000, 1_000_000, 4_000_000, 4_000_000],
    )
    actions = pd.DataFrame([
        {"symbol": "AAPL", "ex_date": date(2020, 8, 31), "action_type": "split",
         "ratio": 4.0, "cash_amount": None},
    ])
    adj = ca.adjust(df, actions, mode="split_div")
    closes = adj["close"].tolist()
    # pre-split prices divided by 4; post-split unchanged
    assert closes == [100.0, 101.0, 101.0, 102.0]
    # no 4x discontinuity at the boundary
    assert abs(closes[2] / closes[1] - 1.0) < 0.05
    # pre-split volume scaled UP by 4 (dollar volume preserved)
    assert adj["volume"].tolist()[:2] == [4_000_000, 4_000_000]


def test_dividend_back_adjust():
    # $2 dividend ex 2024-02-09, close before = 100 -> factor 0.98 on prior bars
    df = _daily(["2024-02-07", "2024-02-08", "2024-02-09"], [99.0, 100.0, 100.0])
    actions = pd.DataFrame([
        {"symbol": "X", "ex_date": date(2024, 2, 9), "action_type": "dividend",
         "ratio": None, "cash_amount": 2.0},
    ])
    adj = ca.adjust(df, actions, mode="split_div")
    assert abs(adj["close"].iloc[1] - 98.0) < 1e-9   # 100 * 0.98
    assert adj["close"].iloc[2] == 100.0             # ex-date bar unchanged
    # split-only mode ignores dividends
    adj2 = ca.adjust(df, actions, mode="split")
    assert adj2["close"].iloc[1] == 100.0


def test_store_roundtrip(tmp_path):
    db = tmp_path / "manifest.db"
    n = ca.store_actions([
        {"symbol": "AAPL", "ex_date": date(2020, 8, 31), "action_type": "split",
         "ratio": 4.0, "cash_amount": None},
        {"symbol": "AAPL", "ex_date": date(2024, 2, 9), "action_type": "dividend",
         "ratio": None, "cash_amount": 0.24},
    ], db_path=db)
    assert n == 2
    loaded = ca.load_actions("AAPL", db_path=db)
    assert len(loaded) == 2
    assert set(loaded["action_type"]) == {"split", "dividend"}
    # upsert is idempotent (same UNIQUE key)
    ca.store_actions([{"symbol": "AAPL", "ex_date": date(2020, 8, 31),
                       "action_type": "split", "ratio": 4.0, "cash_amount": None}], db_path=db)
    assert len(ca.load_actions("AAPL", db_path=db)) == 2
