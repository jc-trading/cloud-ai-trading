"""get_bars must not let the read window change the prices it returns.

corporate_actions.adjust derives each dividend factor from the last close
strictly before the ex-date *within the frame it is handed*. If the daily read
is truncated at `end` before adjusting, that close is a different bar and every
price in the window shifts. The contract is: adjust the FULL history, then slice.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quant import config
from quant.data import bars, corporate_actions, store

_SIP = config.PROVIDER_HISTORICAL


def _daily(days, closes) -> pd.DataFrame:
    ts = pd.DatetimeIndex(
        [pd.Timestamp(f"{d} 00:00", tz="America/New_York") for d in days]
    ).tz_convert("UTC")
    return pd.DataFrame({
        "ts": ts, "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
        "volume": [1000.0] * len(closes), "vwap": closes,
        "trade_count": [5.0] * len(closes),
    })


def test_truncated_window_matches_full_history_sliced(tmp_store):
    """The dividend's ex-date sits AFTER the requested end — the classic case
    where a windowed adjust picks the wrong close_before and silently rescales."""
    days = ["2025-01-02", "2025-01-03", "2025-02-03", "2025-02-04", "2025-03-03"]
    store.write_frame("DIV", "daily", _daily(days, [100.0, 110.0, 200.0, 210.0, 300.0]),
                      provider=_SIP)
    corporate_actions.store_actions([{
        "symbol": "DIV", "ex_date": date(2025, 2, 4), "action_type": "dividend",
        "ratio": None, "cash_amount": 5.0}], db_path=config.ACTIONS_DB)

    cut = "2025-01-03"
    windowed = bars.get_bars("DIV", "1d", end=cut)
    full_then_sliced = store.slice_range(bars.get_bars("DIV", "1d"), None, cut)

    pd.testing.assert_frame_equal(windowed, full_then_sliced, check_dtype=True)
    # and the factor is the real one: close_before = the 2025-02-03 bar, not the
    # last bar of the truncated window
    assert windowed["close"].iloc[0] == pytest.approx(100.0 * (1 - 5.0 / 200.0))


def test_start_and_end_windows_are_pure_slices(tmp_store):
    days = ["2025-01-02", "2025-01-03", "2025-02-03", "2025-02-04", "2025-03-03"]
    store.write_frame("DIV2", "daily", _daily(days, [100.0, 110.0, 200.0, 210.0, 300.0]),
                      provider=_SIP)
    corporate_actions.store_actions([
        {"symbol": "DIV2", "ex_date": date(2025, 2, 4), "action_type": "dividend",
         "ratio": None, "cash_amount": 5.0},
        {"symbol": "DIV2", "ex_date": date(2025, 3, 3), "action_type": "split",
         "ratio": 2.0, "cash_amount": None},
    ], db_path=config.ACTIONS_DB)

    full = bars.get_bars("DIV2", "1d")
    for start, end in [(None, "2025-01-03"), ("2025-01-03", "2025-02-04"),
                       ("2025-02-03", None), ("2025-01-02", "2025-03-03")]:
        pd.testing.assert_frame_equal(
            bars.get_bars("DIV2", "1d", start=start, end=end),
            store.slice_range(full, start, end), check_dtype=True,
            obj=f"get_bars(start={start!r}, end={end!r})")


def test_intraday_adjustment_is_refused(tmp_store):
    with pytest.raises(ValueError, match="served RAW"):
        bars.get_bars("AAA", "1min", adjust="split_div")
    with pytest.raises(ValueError, match="served RAW"):
        bars.get_bars("AAA", "5m", adjust="split")
    # explicit RAW is the supported intraday call and returns cleanly
    assert bars.get_bars("AAA", "1min", adjust="none").empty


# --- the same invariant against the real migrated store --------------------

_LIVE_SYMBOLS = ("MSFT", "JNJ", "KO", "PG")


def _live_symbol_with_dividends() -> str | None:
    if not config.BARS_ROOT.exists():
        return None
    for symbol in _LIVE_SYMBOLS:
        actions = corporate_actions.load_actions(symbol)
        if actions.empty or not (actions["action_type"] == "dividend").any():
            continue
        if not store.read_bars(symbol, "daily").empty:
            return symbol
    return None


@pytest.mark.parametrize("cut", ["2023-01-31", "2024-06-28", "2025-03-31"])
def test_live_store_window_is_a_pure_slice(cut):
    symbol = _live_symbol_with_dividends()
    if symbol is None:
        pytest.skip("no live dividend-paying symbol in the store")
    windowed = bars.get_bars(symbol, "1d", end=cut)
    full_then_sliced = store.slice_range(bars.get_bars(symbol, "1d"), None, cut)
    assert not windowed.empty
    pd.testing.assert_frame_equal(windowed, full_then_sliced, check_dtype=True,
                                  obj=f"get_bars({symbol!r}, end={cut!r})")
