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


# --- v3.1 liquidity ranking -------------------------------------------------

def _reader(volumes):
    """Fake bars_reader: symbol -> DataFrame with a `volume` column of len>=20."""
    import pandas as pd

    def read(sym):
        v = volumes.get(sym)
        if v is None:
            return pd.DataFrame(columns=["volume"])
        return pd.DataFrame({"volume": [v] * 25})
    return read


def test_top_liquid_ranks_and_slices():
    from quant.data import universe
    vols = {"A": 100e6, "B": 50e6, "C": 40e6, "D": 30e6, "E": 20e6,
            "F": 10e6, "G": 5e6, "H": 4e6, "I": 3e6, "J": 1e6}
    syms = list(vols)
    top20 = universe.top_liquid(syms, 0.20, bars_reader=_reader(vols))
    assert top20 == ["A", "B"]                 # top 2 of 10 by volume
    top50 = universe.top_liquid(syms, 0.50, bars_reader=_reader(vols))
    assert top50 == ["A", "B", "C", "D", "E"]


def test_top_liquid_drops_symbols_without_enough_history():
    import pandas as pd
    from quant.data import universe

    def reader(sym):
        if sym == "SHORT":
            return pd.DataFrame({"volume": [1e9] * 5})   # < window bars
        return pd.DataFrame({"volume": [1e6] * 25})
    out = universe.top_liquid(["SHORT", "A", "B"], 1.0, bars_reader=reader)
    assert "SHORT" not in out and set(out) == {"A", "B"}
