"""Score -> win-probability calibration for a group's closed stock trades.

Isotonic (pool-adjacent-violators) fit over the entry-day confidence score,
then small blocks are folded into neighbours so every probability rests on at
least CALIB_MIN_BLOCK trades. Pure numpy/python, no I/O; every count is a
plain int so the result serialises straight into JSONB.
"""

from __future__ import annotations

import numpy as np

CALIB_MIN_BLOCK = 20
CALIB_MIN_TRADES = 100


def _merge(a: dict, b: dict) -> dict:
    return {"lo": a["lo"], "hi": b["hi"], "n": a["n"] + b["n"], "wins": a["wins"] + b["wins"]}


def _blocks(scores: list[float], wins: list[int]) -> list[dict]:
    """Tied scores first, then PAV on strict violations (integer
    cross-multiplication, never float division), then equal-p neighbours,
    then every block under CALIB_MIN_BLOCK into its right (else left)
    neighbour until none is too small or one block remains."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    tied: list[dict] = []
    for i in order:
        s, w = scores[i], wins[i]
        if tied and tied[-1]["hi"] == s:
            tied[-1]["n"] += 1
            tied[-1]["wins"] += w
        else:
            tied.append({"lo": s, "hi": s, "n": 1, "wins": w})
    stack: list[dict] = []
    for b in tied:
        stack.append(b)
        while len(stack) > 1 and stack[-2]["wins"] * stack[-1]["n"] > stack[-1]["wins"] * stack[-2]["n"]:
            right = stack.pop()
            stack[-1] = _merge(stack[-1], right)
    equal: list[dict] = []
    for b in stack:
        if equal and equal[-1]["wins"] * b["n"] == b["wins"] * equal[-1]["n"]:
            equal[-1] = _merge(equal[-1], b)
        else:
            equal.append(b)
    blocks = equal
    while len(blocks) > 1:
        small = next((i for i, b in enumerate(blocks) if b["n"] < CALIB_MIN_BLOCK), None)
        if small is None:
            break
        if small + 1 < len(blocks):
            blocks[small:small + 2] = [_merge(blocks[small], blocks[small + 1])]
        else:
            blocks[small - 1:small + 1] = [_merge(blocks[small - 1], blocks[small])]
    return [{**b, "p": b["wins"] / b["n"]} for b in blocks]


def prob(cal: dict, s: float) -> float | None:
    blocks = cal["blocks"]
    if not blocks:
        return None
    hit = blocks[0]
    for b in blocks:
        if b["lo"] <= s:
            hit = b
    return hit["p"]


def score_threshold(cal: dict, p: float) -> float | None:
    return next((b["lo"] for b in cal["blocks"] if b["p"] >= p), None)


def max_p(cal: dict) -> float | None:
    return max((b["p"] for b in cal["blocks"]), default=None)


def _brier(cal: dict, scores: list[float], wins: list[int]) -> float | None:
    if not scores or not cal["blocks"]:
        return None
    return float(np.mean([(prob(cal, s) - w) ** 2 for s, w in zip(scores, wins)]))


def fit(scores, wins, window_ids=None) -> dict:
    """Calibrate scores against wins. With ``window_ids``, also report the
    leave-one-window-out Brier (each window predicted by a fit on the others)
    next to the same-protocol base-rate Brier."""
    scores = [float(s) for s in scores]
    wins = [int(bool(w)) for w in wins]
    n = len(scores)
    blocks = [{"lo": float(b["lo"]), "hi": float(b["hi"]), "n": int(b["n"]),
               "wins": int(b["wins"]), "p": float(b["p"])}
              for b in (_blocks(scores, wins) if n else [])]
    cal = {
        "status": "ok" if n >= CALIB_MIN_TRADES and len(blocks) >= 2 else "insufficient",
        "n": n,
        "base_rate": sum(wins) / n if n else None,
        "blocks": blocks,
        "brier_in_sample": None,
        "loo_window_brier": None,
        "loo_base_rate_brier": None,
    }
    cal["brier_in_sample"] = _brier(cal, scores, wins)
    if window_ids is not None and n:
        ids = list(window_ids)
        errs, base_errs = [], []
        for w in sorted(set(ids), key=str):
            train = [i for i in range(n) if ids[i] != w]
            test = [i for i in range(n) if ids[i] == w]
            if not train:
                continue
            other = fit([scores[i] for i in train], [wins[i] for i in train])
            for i in test:
                errs.append((prob(other, scores[i]) - wins[i]) ** 2)
                base_errs.append((other["base_rate"] - wins[i]) ** 2)
        if errs:
            cal["loo_window_brier"] = float(np.mean(errs))
            cal["loo_base_rate_brier"] = float(np.mean(base_errs))
    return cal
