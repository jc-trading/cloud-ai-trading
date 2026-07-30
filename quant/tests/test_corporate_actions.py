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


def test_reverse_split_back_adjust():
    # GE-style 1:8 reverse split (ratio = new/old = 0.125): raw jumps 13 -> 104.
    # Back-adjusted, pre-split bars scale UP x8 so the series is continuous.
    df = _daily(
        ["2021-07-30", "2021-08-02", "2021-08-03"],
        [13.0, 104.0, 105.0],
        vols=[8_000_000, 1_000_000, 1_000_000],
    )
    actions = pd.DataFrame([
        {"symbol": "GE", "ex_date": date(2021, 8, 2), "action_type": "split",
         "ratio": 0.125, "cash_amount": None},
    ])
    adj = ca.adjust(df, actions, mode="split_div")
    closes = adj["close"].tolist()
    assert closes == [104.0, 104.0, 105.0]
    # pre-split volume scaled DOWN x8 (dollar volume preserved)
    assert adj["volume"].iloc[0] == 1_000_000


def test_multiple_actions_compound():
    # a 4:1 split then a later 2:1 split: earliest bars carry BOTH factors
    df = _daily(
        ["2020-01-02", "2020-06-01", "2021-06-01"],
        [800.0, 200.0, 100.0],
    )
    actions = pd.DataFrame([
        {"symbol": "X", "ex_date": date(2020, 6, 1), "action_type": "split",
         "ratio": 4.0, "cash_amount": None},
        {"symbol": "X", "ex_date": date(2021, 6, 1), "action_type": "split",
         "ratio": 2.0, "cash_amount": None},
    ])
    adj = ca.adjust(df, actions, mode="split_div")
    assert adj["close"].tolist() == [100.0, 100.0, 100.0]


class _FakeCAClient:
    """Mimics alpaca-py: returns a dict payload with all four buckets."""

    def get_corporate_actions(self, req):
        return {
            "forward_splits": [
                {"symbol": "AAPL", "ex_date": date(2020, 8, 31),
                 "new_rate": 4.0, "old_rate": 1.0},
            ],
            "reverse_splits": [
                {"symbol": "GE", "ex_date": date(2021, 8, 2),
                 "new_rate": 1.0, "old_rate": 8.0},
            ],
            "stock_dividends": [
                # 5% stock dividend == a 1.05:1 split economically
                {"symbol": "SD", "ex_date": date(2022, 3, 1), "rate": 0.05},
            ],
            "cash_dividends": [
                {"symbol": "AAPL", "ex_date": date(2024, 2, 9), "rate": 0.24},
            ],
        }


def test_fetch_actions_reads_all_four_buckets():
    out = ca.fetch_actions(["AAPL", "GE", "SD"], date(2016, 1, 1), date(2026, 1, 1),
                           client=_FakeCAClient())
    by_sym = {(a["symbol"], a["action_type"]): a for a in out}
    assert len(out) == 4
    assert by_sym[("AAPL", "split")]["ratio"] == 4.0
    assert by_sym[("GE", "split")]["ratio"] == 0.125          # reverse: new/old
    assert abs(by_sym[("SD", "split")]["ratio"] - 1.05) < 1e-12
    assert by_sym[("AAPL", "dividend")]["cash_amount"] == 0.24


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
