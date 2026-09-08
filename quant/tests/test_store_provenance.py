"""write_bars provenance + durability (方案 Phase 2 #2).

A bar file has exactly one source, and that fact survives concurrent writers,
crashes mid-write and a re-source. Everything here runs on the in-memory
registry fake — no PostgreSQL.
"""

from __future__ import annotations

import threading

import pandas as pd
import pytest

from quant import config
from quant.data import store

_SIP = "alpaca:sip"
_IEX = "alpaca:iex"


def _bars(minutes, base=100.0):
    ts = pd.DatetimeIndex(
        [pd.Timestamp(f"2026-07-24 {m}", tz="America/New_York") for m in minutes]
    ).tz_convert("UTC")
    n = len(minutes)
    return pd.DataFrame({
        "ts": ts,
        "open": [base + i for i in range(n)], "high": [base + i + 1 for i in range(n)],
        "low": [base + i - 1 for i in range(n)], "close": [base + i + 0.5 for i in range(n)],
        "volume": [10.0 * (i + 1) for i in range(n)], "vwap": [base + i for i in range(n)],
        "trade_count": [3.0] * n,
    })


DAY = "2026-07-24"


def test_same_provider_merge_is_idempotent(tmp_store):
    first = _bars(["09:30", "09:31", "09:32"])
    assert store.write_bars("AAA", "1min", DAY, first, provider=_SIP) == 3
    # re-writing the same rows changes nothing; an overlapping window merges
    assert store.write_bars("AAA", "1min", DAY, first, provider=_SIP) == 3
    row_after_replay = tmp_store.get_row("AAA", "1min", DAY)

    second = _bars(["09:32", "09:33"], base=200.0)
    assert store.write_bars("AAA", "1min", DAY, second, provider=_SIP) == 4

    out = store.read_bars("AAA", "1min", DAY, DAY)
    assert len(out) == 4
    assert out["ts"].is_monotonic_increasing
    assert out["ts"].duplicated().sum() == 0
    assert out.loc[out["ts"] == first["ts"].iloc[2], "open"].iloc[0] == 200.0  # keep=last

    row = tmp_store.get_row("AAA", "1min", DAY)
    assert row.row_count == 4
    assert row.path == "AAA/1min/2026-07-24.parquet"     # relative to BARS_ROOT
    assert row.first_ts == out["ts"].min() and row.last_ts == out["ts"].max()
    assert row.checksum != row_after_replay.checksum      # content moved
    assert row.status == "ok"


def test_replaying_the_identical_frame_keeps_the_checksum_stable(tmp_store):
    df = _bars(["09:30", "09:31"])
    store.write_bars("AAA", "1min", DAY, df, provider=_SIP)
    before = tmp_store.get_row("AAA", "1min", DAY).checksum
    store.write_bars("AAA", "1min", DAY, df, provider=_SIP)
    assert tmp_store.get_row("AAA", "1min", DAY).checksum == before


def test_foreign_provider_is_refused(tmp_store):
    store.write_bars("AAA", "1min", DAY, _bars(["09:30"]), provider=_SIP)
    with pytest.raises(store.ProviderMismatch):
        store.write_bars("AAA", "1min", DAY, _bars(["09:31"]), provider=_IEX)
    # the refused write left the file and its registry row untouched
    assert len(store.read_bars("AAA", "1min", DAY, DAY)) == 1
    assert tmp_store.get_row("AAA", "1min", DAY).provider == _SIP


def test_replace_overwrites_and_flips_the_provider(tmp_store):
    store.write_bars("AAA", "1min", DAY, _bars(["09:30", "09:31", "09:32"]),
                     provider=_IEX)
    assert store.write_bars("AAA", "1min", DAY, _bars(["09:35"], base=500.0),
                            provider=_SIP, replace=True) == 1

    out = store.read_bars("AAA", "1min", DAY, DAY)
    assert len(out) == 1                                  # whole file replaced
    assert out["open"].iloc[0] == 500.0
    row = tmp_store.get_row("AAA", "1min", DAY)
    assert row.provider == _SIP and row.row_count == 1


def test_parquet_metadata_matches_the_registry(tmp_store):
    store.write_bars("AAA", "1min", DAY, _bars(["09:30"]), provider=_IEX)
    path = store.bar_path("AAA", "1min", DAY)
    assert store.parquet_provider(path) == tmp_store.get_row("AAA", "1min", DAY).provider == _IEX


def test_unregistered_file_is_still_provenance_checked(tmp_store):
    """A file whose registry row is missing (registry restored from an older
    dump, row deleted by hand) must not become a free pass to mix sources."""
    store.write_bars("AAA", "1min", DAY, _bars(["09:30"]), provider=_IEX)
    tmp_store.delete("AAA", "1min", DAY)

    with pytest.raises(store.ProviderMismatch):
        store.write_bars("AAA", "1min", DAY, _bars(["09:31"]), provider=_SIP)
    assert store.parquet_provider(store.bar_path("AAA", "1min", DAY)) == _IEX
    assert len(store.read_bars("AAA", "1min", DAY, DAY)) == 1
    # same provider still merges, and re-registers the orphaned file
    assert store.write_bars("AAA", "1min", DAY, _bars(["09:31"]), provider=_IEX) == 2
    assert tmp_store.get_row("AAA", "1min", DAY).provider == _IEX


def test_lock_files_live_outside_the_bar_tree(tmp_store):
    store.write_bars("AAA", "1min", DAY, _bars(["09:30"]), provider=_SIP)
    assert list(store.bar_dir("AAA", "1min").iterdir()) == \
        [store.bar_path("AAA", "1min", DAY)]
    assert store.lock_path("AAA", "1min", DAY).exists()
    assert store.lock_path("AAA", "1min", DAY).parent == config.BARS_ROOT / "_locks"


def test_failed_write_leaves_no_temp_file(tmp_store, monkeypatch):
    import pyarrow.parquet as pq

    def boom(*a, **kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(pq, "write_table", boom)
    with pytest.raises(OSError):
        store.write_bars("AAA", "1min", DAY, _bars(["09:30"]), provider=_SIP)
    assert not list(store.bar_dir("AAA", "1min").glob("*.tmp-*"))


def test_write_lands_atomically(tmp_store, monkeypatch):
    """A crash between the temp write and the rename must leave the previous
    file whole — readers never see a half-written parquet."""
    store.write_bars("AAA", "1min", DAY, _bars(["09:30", "09:31"]), provider=_SIP)
    path = store.bar_path("AAA", "1min", DAY)
    before = path.read_bytes()

    real_replace = store.os.replace

    def boom(src, dst):
        raise OSError("disk gave up between write and rename")

    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(OSError):
        store.write_bars("AAA", "1min", DAY, _bars(["09:32"]), provider=_SIP)
    monkeypatch.setattr(store.os, "replace", real_replace)

    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp-*"))
    assert len(store.read_bars("AAA", "1min", DAY, DAY)) == 2


def test_concurrent_writers_do_not_lose_rows(tmp_store):
    """Two writers racing on one file: without the flock their read-merge-write
    interleaves and the later write drops the earlier one's rows."""
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def write(minutes):
        try:
            barrier.wait(timeout=10)
            store.write_bars("AAA", "1min", DAY, _bars(minutes), provider=_SIP)
        except BaseException as exc:            # surfaced after the join
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(m,))
               for m in (["09:30", "09:31"], ["09:40", "09:41"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    out = store.read_bars("AAA", "1min", DAY, DAY)
    assert len(out) == 4                        # both writers' rows survived
    assert tmp_store.get_row("AAA", "1min", DAY).row_count == 4


def test_registry_is_injectable_per_call(tmp_store):
    from quant.tests.conftest import FakeRegistry

    other = FakeRegistry()
    store.write_bars("AAA", "daily", "2026", _bars(["09:30"]), provider=_SIP,
                     registry=other)
    assert other.get_row("AAA", "daily", "2026") is not None
    assert tmp_store.get_row("AAA", "daily", "2026") is None


def test_empty_frame_writes_nothing(tmp_store):
    empty = _bars(["09:30"]).iloc[0:0]
    assert store.write_bars("AAA", "1min", DAY, empty, provider=_SIP) == 0
    assert not store.bar_path("AAA", "1min", DAY).exists()
    assert tmp_store.get_row("AAA", "1min", DAY) is None
