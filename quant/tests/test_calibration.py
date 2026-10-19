"""P3b: isotonic score -> win-probability calibration."""

import json

import numpy as np
import pytest

from quant.backtest import calibration as cal


@pytest.fixture
def tiny_blocks(monkeypatch):
    monkeypatch.setattr(cal, "CALIB_MIN_BLOCK", 1)


def _shape(c):
    return [(b["lo"], b["hi"], b["n"], b["wins"]) for b in c["blocks"]]


def test_pav_six_points_by_hand(tiny_blocks):
    c = cal.fit([1, 2, 3, 4, 5, 6], [1, 0, 1, 0, 1, 1])
    assert _shape(c) == [(1.0, 4.0, 4, 2), (5.0, 6.0, 2, 2)]
    assert [b["p"] for b in c["blocks"]] == [0.5, 1.0]


def test_tied_scores_form_one_block_first(tiny_blocks):
    c = cal.fit([3, 1, 3, 3], [1, 0, 0, 1])
    assert _shape(c) == [(1.0, 1.0, 1, 0), (3.0, 3.0, 3, 2)]


def test_equal_probability_neighbours_merge_exactly(tiny_blocks):
    # 1/3 then 2/6: equal by integer cross-multiplication -> one block
    c = cal.fit([1, 1, 1, 2, 2, 2, 2, 2, 2], [1, 0, 0, 1, 1, 0, 0, 0, 0])
    assert _shape(c) == [(1.0, 2.0, 9, 3)]


def test_min_block_merge_keeps_blocks_large_and_monotone():
    rng = np.random.default_rng(7)
    scores = rng.uniform(0, 100, 400)
    wins = rng.uniform(0, 1, 400) < scores / 100
    c = cal.fit(scores, wins)
    assert len(c["blocks"]) >= 2
    assert all(b["n"] >= cal.CALIB_MIN_BLOCK for b in c["blocks"])
    ps = [b["p"] for b in c["blocks"]]
    assert ps == sorted(ps)
    assert sum(b["n"] for b in c["blocks"]) == 400
    assert all(a["hi"] < b["lo"] for a, b in zip(c["blocks"], c["blocks"][1:]))


def test_small_last_block_folds_into_its_left_neighbour(monkeypatch):
    monkeypatch.setattr(cal, "CALIB_MIN_BLOCK", 3)
    c = cal.fit([1, 2, 3, 4, 4, 4, 4, 5], [0, 0, 0, 1, 1, 0, 0, 1])
    assert _shape(c) == [(1.0, 3.0, 3, 0), (4.0, 5.0, 5, 3)]


def test_threshold_boundaries_and_none(tiny_blocks):
    c = cal.fit([1, 2, 3, 4, 5, 6], [1, 0, 1, 0, 1, 1])
    assert cal.score_threshold(c, 0.5) == 1.0
    assert cal.score_threshold(c, 0.75) == 5.0
    assert cal.score_threshold(c, 1.0) == 5.0
    assert cal.score_threshold(c, 1.01) is None
    assert cal.max_p(c) == 1.0
    empty = cal.fit([], [])
    assert cal.score_threshold(empty, 0.1) is None
    assert cal.max_p(empty) is None
    assert cal.prob(empty, 50) is None


def test_status_needs_100_trades_and_two_blocks():
    scores = list(range(60))
    few = cal.fit(scores, [0] * 30 + [1] * 30)
    assert len(few["blocks"]) == 2 and few["status"] == "insufficient"
    many = cal.fit(list(range(120)), [0] * 60 + [1] * 60)
    assert many["status"] == "ok" and many["n"] == 120 and many["base_rate"] == 0.5


@pytest.mark.parametrize("outcome", [0, 1])
def test_all_wins_or_all_losses_is_one_block_and_insufficient(outcome):
    c = cal.fit(list(range(150)), [outcome] * 150)
    assert len(c["blocks"]) == 1
    assert c["status"] == "insufficient"
    assert c["blocks"][0]["p"] == float(outcome)


def test_leave_one_window_out_brier_by_hand(tiny_blocks):
    scores = [1, 2, 1, 2, 1, 2]
    wins = [0, 1, 0, 1, 0, 0]
    ids = ["A", "A", "B", "B", "C", "C"]
    c = cal.fit(scores, wins, window_ids=ids)
    assert c["loo_window_brier"] == pytest.approx(1.5 / 6)
    assert c["loo_base_rate_brier"] == pytest.approx(1.75 / 6)
    assert cal.fit(scores, wins)["loo_window_brier"] is None


def test_prob_below_first_block_uses_first_block(tiny_blocks):
    c = cal.fit([10, 20, 30, 40], [0, 0, 1, 1])
    assert cal.prob(c, 5) == 0.0
    assert cal.prob(c, 35) == 1.0
    assert cal.prob(c, 999) == 1.0


def test_numpy_inputs_are_stored_as_plain_python_numbers():
    c = cal.fit(np.arange(150, dtype=np.float64), np.array([0] * 75 + [1] * 75, dtype=np.int64),
                window_ids=np.repeat(np.arange(3), 50))
    for b in c["blocks"]:
        assert type(b["n"]) is int and type(b["wins"]) is int
        assert type(b["lo"]) is float and type(b["p"]) is float
    assert type(c["n"]) is int


def test_output_is_strict_json():
    c = cal.fit(np.arange(150) / 3.0, np.arange(150) % 3 == 0, window_ids=np.arange(150) % 5)
    json.dumps(c, allow_nan=False)
    json.dumps(cal.fit([], []), allow_nan=False)
