"""Scoreboard metrics (design §1.3, §11) — all vs SPY buy-and-hold.

Risk-adjusted return is the real bar: beating SPY on Sharpe / return-over-maxDD,
not on absolute return. Pure functions over an equity curve + trade list.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_TRADING_DAYS = 252


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_date: object
    exit_date: object
    entry_price: float
    exit_price: float
    shares: float
    r_unit: float          # initial per-share risk
    pnl: float             # net of costs
    exit_reason: str

    @property
    def r_multiple(self) -> float:
        if self.r_unit <= 0 or self.shares <= 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.r_unit


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = len(equity) / _TRADING_DAYS
    if years <= 0:
        return 0.0
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())   # negative


def _daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def sharpe(equity: pd.Series, rf: float = 0.0) -> float:
    r = _daily_returns(equity)
    if r.std(ddof=0) == 0 or r.empty:
        return 0.0
    excess = r - rf / _TRADING_DAYS
    return float(np.sqrt(_TRADING_DAYS) * excess.mean() / r.std(ddof=0))


def sortino(equity: pd.Series, rf: float = 0.0) -> float:
    r = _daily_returns(equity)
    downside = r[r < 0]
    dd = downside.std(ddof=0)
    if dd == 0 or r.empty:
        return 0.0
    excess = r - rf / _TRADING_DAYS
    return float(np.sqrt(_TRADING_DAYS) * excess.mean() / dd)


def win_rate(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    return sum(1 for t in trades if t.pnl > 0) / len(trades)


def profit_factor(trades: list[Trade]) -> float:
    gains = sum(t.pnl for t in trades if t.pnl > 0)
    losses = -sum(t.pnl for t in trades if t.pnl < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def avg_r(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    return float(np.mean([t.r_multiple for t in trades]))


def summary(equity: pd.Series, trades: list[Trade],
            benchmark: pd.Series | None = None) -> dict:
    """Full scoreboard dict. If benchmark (SPY equity curve, same dates) given,
    include its CAGR/Sharpe/MaxDD for the head-to-head."""
    mdd = max_drawdown(equity)
    out = {
        "cagr": cagr(equity),
        "sharpe": sharpe(equity),
        "sortino": sortino(equity),
        "max_drawdown": mdd,
        "return_over_maxdd": (cagr(equity) / abs(mdd)) if mdd < 0 else float("inf"),
        "win_rate": win_rate(trades),
        "profit_factor": profit_factor(trades),
        "avg_r": avg_r(trades),
        "num_trades": len(trades),
        "final_equity": float(equity.iloc[-1]) if len(equity) else 0.0,
    }
    if benchmark is not None and len(benchmark) > 1:
        out["spy_cagr"] = cagr(benchmark)
        out["spy_sharpe"] = sharpe(benchmark)
        out["spy_max_drawdown"] = max_drawdown(benchmark)
        out["spy_return_over_maxdd"] = (
            cagr(benchmark) / abs(max_drawdown(benchmark))
            if max_drawdown(benchmark) < 0 else float("inf"))
        out["beats_spy_sharpe"] = out["sharpe"] > out["spy_sharpe"]
        out["beats_spy_return_over_maxdd"] = out["return_over_maxdd"] > out["spy_return_over_maxdd"]
    return out
