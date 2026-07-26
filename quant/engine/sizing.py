"""Position sizing + pyramiding — pure functions (design §8.1; A2/A3/A7).

Long-only. Size = min(risk-budget / per-share-risk, equity/slots, settled cash,
liquidity cap). v1 uses an equal-weight GATE (no confidence scaling until it is
backtest-calibrated). Pyramiding adds to WINNERS only and re-raises the stop so
combined entry-risk stays within the 3% budget.
"""

from __future__ import annotations

import math

from quant import config


def concurrent_slots(equity: float, ladder=config.POSITION_LADDER) -> int:
    """Step function: highest ladder tier whose threshold <= equity (min tier
    applies below the first threshold). $2k->3, $5k->4, $10k->5, $20k->10."""
    slots = ladder[0][1]
    for threshold, s in ladder:
        if equity >= threshold:
            slots = s
    return slots


def position_size(equity: float, entry: float, stop: float, *,
                  risk_pct: float = config.PER_TRADE_RISK_PCT,
                  slots: int | None = None, settled_cash: float | None = None,
                  adv: float | None = None, adv_cap_pct: float = 0.01,
                  fractional: bool = config.FRACTIONAL_SHARES) -> float:
    """Shares to buy for a long entry. Returns 0 if the trade can't be sized."""
    if entry <= 0 or stop <= 0 or entry <= stop:
        return 0.0
    if slots is None:
        slots = concurrent_slots(equity)
    if settled_cash is None:
        settled_cash = equity

    per_share_risk = entry - stop
    caps = [
        (equity * risk_pct) / per_share_risk,   # fixed-fractional risk budget
        (equity / slots) / entry,               # ladder dollar cap
        settled_cash / entry,                    # available cash
    ]
    if adv is not None:
        caps.append((adv * adv_cap_pct) / entry)  # liquidity cap (% of ADV)
    shares = min(caps)
    if not fractional:
        shares = math.floor(shares)
    return max(shares, 0.0)


def pyramid_allowed(avg_cost: float, current_price: float, adds_done: int, *,
                    max_adds: int = config.MAX_PYRAMID_ADDS_PER_SYMBOL) -> bool:
    """Add only to winners (price above average cost) and within the add cap."""
    return current_price > avg_cost and adds_done < max_adds


def raise_stop_for_combined_risk(shares_total: float, avg_cost: float, equity: float,
                                 current_stop: float, *,
                                 risk_pct: float = config.PER_TRADE_RISK_PCT) -> float:
    """After a pyramid add, the minimum stop that keeps combined entry-risk
    (shares_total * (avg_cost - stop)) within the risk budget. Never lowers the
    existing stop."""
    if shares_total <= 0:
        return current_stop
    budget = equity * risk_pct
    min_stop = avg_cost - budget / shares_total
    return max(current_stop, min_stop)


def blend_avg_cost(shares_a: float, cost_a: float, shares_b: float, cost_b: float) -> float:
    total = shares_a + shares_b
    if total <= 0:
        return 0.0
    return (shares_a * cost_a + shares_b * cost_b) / total
