"""R0-4 universe tests: point-in-time membership + survivorship-free union.
Uses a synthetic changelog (no network)."""

import pytest

from quant.data import universe


@pytest.fixture
def synthetic_csv(tmp_path, monkeypatch):
    csv = tmp_path / "changes.csv"
    csv.write_text(
        "date,tickers\n"
        '2020-01-01,"AAA,BBB,DDD"\n'
        '2020-06-01,"AAA,CCC,DDD"\n'   # BBB removed, CCC added
        '2021-01-01,"AAA,CCC,EEE"\n'   # DDD removed, EEE added
    )
    monkeypatch.setattr(universe, "_CSV_PATH", csv)
    universe._changes.cache_clear()
    yield
    universe._changes.cache_clear()


def test_constituents_on(synthetic_csv):
    assert universe.constituents_on("2020-03-01") == ["AAA", "BBB", "DDD"]
    assert universe.constituents_on("2020-06-01") == ["AAA", "CCC", "DDD"]  # on the change date
    assert universe.constituents_on("2020-12-31") == ["AAA", "CCC", "DDD"]
    assert universe.constituents_on("2021-05-01") == ["AAA", "CCC", "EEE"]
    # before the first snapshot -> empty
    assert universe.constituents_on("2019-01-01") == []


def test_union_is_survivorship_free(synthetic_csv):
    # union across the whole window includes BBB and DDD even though they were
    # later removed — the strategy would have traded them while they were members.
    assert universe.all_symbols_in_range("2020-01-01", "2021-12-31") == [
        "AAA", "BBB", "CCC", "DDD", "EEE"
    ]
    # a sub-window starting after BBB left still counts BBB via the start-snapshot
    # only if it was active at start; here start 2020-07 -> BBB already gone
    assert universe.all_symbols_in_range("2020-07-01", "2021-12-31") == [
        "AAA", "CCC", "DDD", "EEE"
    ]
