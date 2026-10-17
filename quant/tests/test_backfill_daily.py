"""Daily backfill: the optional pause between fresh batches (P2 initial load
paces ~11k fresh symbols against the free-tier rate limit)."""

from datetime import datetime, timezone

import pandas as pd

from quant.data import backfill, fetch

NOW = datetime(2026, 10, 16, 22, 0, tzinfo=timezone.utc)


def _bars():
    return pd.DataFrame({"ts": [pd.Timestamp("2026-10-15 04:00", tz="UTC")],
                         "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                         "volume": [100.0], "vwap": [1.0], "trade_count": [1]})


def _run(monkeypatch, **kw):
    sleeps = []
    monkeypatch.setattr(fetch, "fetch_daily_multi",
                        lambda batch, start, now=None: {s: _bars() for s in batch})
    monkeypatch.setattr(backfill.time, "sleep", lambda s: sleeps.append(s))
    st = backfill.run_daily_backfill(["AAA", "BBB", "CCC"], now=NOW, batch_size=2,
                                     progress=lambda m: None, **kw)
    return st, sleeps


def test_pause_after_every_fresh_batch(tmp_store, monkeypatch):
    st, sleeps = _run(monkeypatch, pause_s=3.0)
    assert st.fresh == 3 and st.stored == 3
    assert sleeps == [3.0, 3.0]


def test_no_pause_by_default(tmp_store, monkeypatch):
    st, sleeps = _run(monkeypatch)
    assert st.stored == 3 and sleeps == []
