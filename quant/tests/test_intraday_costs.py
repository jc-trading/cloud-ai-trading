"""P5a: the 1min cost model — tiered bps per side with a one-tick floor."""

from dataclasses import asdict

import pytest

from quant.backtest.costs import CostModel
from quant.backtest.intraday_costs import IntradayCostModel

C = IntradayCostModel()


def test_mega_liquid_tier():
    assert C.entry_fill(100.0, 6e8) == pytest.approx(100.07, abs=1e-12)
    assert C.exit_fill(100.0, 6e8) == pytest.approx(99.93, abs=1e-12)


def test_min_tick_floor_on_a_cheap_stock():
    assert C.entry_fill(2.0) == pytest.approx(2.01, abs=1e-12)
    assert C.exit_fill(2.0) == pytest.approx(1.99, abs=1e-12)


def test_unknown_adv_uses_the_fallback_half_spread():
    assert C.entry_fill(100.0, None) == pytest.approx(100.08, abs=1e-12)


@pytest.mark.parametrize("price, adv", [(100.0, 6e8), (50.0, 2e8), (2.0, None), (7.5, 1e6)])
def test_entry_and_exit_are_symmetric(price, adv):
    assert C.entry_fill(price, adv) - price == pytest.approx(price - C.exit_fill(price, adv))


def test_commission_is_zero():
    assert C.commission(10) == 0


def test_fields_are_the_daily_model_plus_min_tick():
    assert set(asdict(C)) == set(asdict(CostModel())) | {"min_tick"}
    assert C.min_tick == 0.01
