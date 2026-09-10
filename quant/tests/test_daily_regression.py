"""Phase 0 daily-strategy regression: the market-data refactor must move neither
the engine nor the store.

Two independent assertions (plan Phase 0 #4) — freezing only the outputs would let
a store bug and an engine bug cancel out:

  (a) engine vs FROZEN input bars -> frozen recommendations + backtest metrics.
      Proves the strategy code is unchanged. No cat-data, no PG, no network.
  (b) the live store, re-adjusted with the FROZEN corporate actions, vs the FROZEN
      input bars. Proves the store + its migration are unchanged. Needs the live
      cat-data store, so it skips when that is absent. Actions-snapshot-pinned on
      purpose: signal_cycle syncs corporate actions nightly, and reading the live
      cache here made every new dividend look like a store regression.

Regenerate the goldens with quant/tests/golden/freeze.py.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from quant import config
from quant.tests.golden import freeze

pytestmark = pytest.mark.skipif(not freeze.BARS_PARQUET.exists(),
                                reason="golden baseline not frozen")

_FLOAT_TOL = 1e-9


@pytest.fixture(scope="module")
def frozen_bars() -> pd.DataFrame:
    return pd.read_parquet(freeze.BARS_PARQUET)


@pytest.fixture(scope="module")
def golden_symbols():
    return freeze.load_symbols()


def _assert_same(actual, expected, path="") -> None:
    """Field-by-field comparison that fails on the first differing leaf and names
    it. Floats compare within _FLOAT_TOL so numpy/pandas ULP noise across
    environments cannot mask (or fake) a strategy change; everything else is exact."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected a dict, got {type(actual)}"
        assert sorted(actual) == sorted(expected), f"{path}: key set differs"
        for k in expected:
            _assert_same(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected a list, got {type(actual)}"
        assert len(actual) == len(expected), \
            f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_same(a, e, f"{path}[{i}]")
    elif isinstance(expected, float) and isinstance(actual, (int, float)) \
            and not isinstance(actual, bool):
        assert actual == pytest.approx(expected, rel=_FLOAT_TOL, abs=_FLOAT_TOL), \
            f"{path}: {actual} != {expected}"
    else:
        assert actual == expected, f"{path}: {actual!r} != {expected!r}"


# --- (a) engine vs frozen input bars ---------------------------------------

def test_recommendations_match_golden(frozen_bars, golden_symbols):
    cycles = pytest.importorskip(
        "app.modules.simledger.cycles",
        reason="build_recommendations lives in backend/ (needs SQLAlchemy); run "
               "this file inside the backend container to exercise it")
    symbols, sectors = golden_symbols
    golden = json.loads(freeze.RECS_JSON.read_text())

    bars_fn = cycles.memoized_bars_fn(
        freeze.SESSION_DATE, get_bars=freeze.frozen_get_bars(frozen_bars))
    batch = cycles.build_recommendations(symbols, freeze.SESSION_DATE,
                                         bars_fn=bars_fn, sectors=sectors)
    recs = sorted(batch.rows, key=lambda r: r["symbol"])

    actual = json.loads(json.dumps(recs, default=freeze.json_default))
    _assert_same(actual, golden["recommendations"], "recs")


def test_backtest_metrics_match_golden(frozen_bars, golden_symbols):
    symbols, sectors = golden_symbols
    golden = json.loads(freeze.BACKTEST_JSON.read_text())
    if golden.get("numpy") != np.__version__:
        pytest.skip("backtest golden is container-frozen (numpy ULP divergence): "
                    f"frozen on numpy {golden.get('numpy')}, running {np.__version__}")

    cfg = freeze.sim_config()
    assert freeze.config_dict(cfg) == golden["config"], \
        "the frozen BacktestConfig no longer matches sim_config() — a default drifted"

    summary = freeze.run_backtest(symbols, sectors, cfg,
                                  get_bars=freeze.frozen_get_bars(frozen_bars))
    actual = json.loads(json.dumps(summary, default=freeze.json_default))
    _assert_same(actual, golden["metrics"], "metrics")


# --- (b) live store vs frozen input bars -----------------------------------

@pytest.mark.skipif(not config.BARS_ROOT.exists(),
                    reason="live bar store not present")
def test_live_store_reproduces_frozen_bars(frozen_bars, golden_symbols):
    symbols, _ = golden_symbols
    frozen_by_symbol = dict(list(frozen_bars.groupby("symbol", sort=False)))
    assert sorted(frozen_by_symbol) == sorted(symbols)
    actions = freeze.load_frozen_actions()

    for sym in symbols:
        expected = (frozen_by_symbol[sym].drop(columns=["symbol"])
                    .reset_index(drop=True))
        actual = freeze.bars_from_store(sym, actions)
        assert list(actual.columns) == list(config.BAR_COLUMNS), f"{sym}: columns"
        assert len(actual) == len(expected), f"{sym}: row count"
        assert str(actual["ts"].dt.tz) == "UTC", f"{sym}: ts must stay UTC"
        pd.testing.assert_frame_equal(actual, expected, check_dtype=True,
                                      obj=f"store+frozen actions ({sym!r})")
