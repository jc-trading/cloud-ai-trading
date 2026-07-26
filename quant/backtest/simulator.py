"""Thin daily portfolio simulator (design D1). Deliberately small (<500 lines)
and MUST NOT grow into a general framework — vectorbt/backtesting.py cover
single-symbol sweeps; this covers only what they can't: cross-sectional funnel +
ladder slots + pyramiding + stagnation rotation, reusing the engine/ pure
functions verbatim (架构铁律①).

Discipline enforced here:
  - EXITS before ENTRIES each day (§3.1 #6 before #7)
  - signals decided on D-1 close, filled at D OPEN (next-open, no lookahead §3.2)
  - costs deducted on every fill (§4.6)
  - equity marked-to-market on D close
Cash is modeled simply (immediate availability); T+1 settlement is a live concern
applied in R1, not needed to validate signal edge in R0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant import config
from quant.backtest.costs import CostModel
from quant.backtest.metrics import Trade
from quant.data import bars as barsmod
from quant.engine import funnel as fn
from quant.engine import sizing
from quant.engine import strategy as strat
from quant.engine.exits import (ExitParams, Position, evaluate_exit,
                                maybe_raise_trailing, update_position_bar)
from quant.engine.signal import Direction


@dataclass
class SimConfig:
    start: str
    end: str
    starting_capital: float = config.STARTING_CAPITAL_USD
    adv_window: int = 20
    strategy: strat.StrategyParams = field(default_factory=strat.StrategyParams)
    funnel: fn.FunnelParams = field(default_factory=fn.FunnelParams)
    exits: ExitParams = field(default_factory=ExitParams)
    costs: CostModel = field(default_factory=CostModel)
    risk_pct: float = config.PER_TRADE_RISK_PCT


@dataclass
class SimResult:
    equity: pd.Series
    trades: list[Trade]
    benchmark: pd.Series


def _build_features(symbol: str, sector: str, cfg: SimConfig) -> pd.DataFrame | None:
    """Per-symbol feature frame indexed by date: OHLC + signal fields + funnel
    features. Signals use only past+current bars (no lookahead)."""
    b = barsmod.get_bars(symbol, "1d", end=cfg.end, adjust="split_div")
    if b.empty or len(b) < cfg.strategy.warmup + 5:
        return None
    sig = strat.compute_signals(b, cfg.strategy)
    df = pd.DataFrame({
        "date": b["ts"].dt.tz_convert("America/New_York").dt.date,
        "open": b["open"].values, "high": b["high"].values,
        "low": b["low"].values, "close": b["close"].values,
        "direction": sig["direction"].values,
        "confidence": sig["confidence"].values,
        "atr": sig["atr"].values, "stop_distance": sig["stop_distance"].values,
        "expected_move": sig["expected_move"].values, "ma_slow": sig["ma_slow"].values,
    })
    df["price"] = df["close"]   # funnel's liquidity/penny filter reads 'price'
    dollar_vol = b["close"] * b["volume"]
    df["adv"] = dollar_vol.rolling(cfg.adv_window, min_periods=cfg.adv_window).mean().values
    df["atr_pct"] = (df["atr"] / df["close"]).values
    df["above_rising_ma20"] = ((df["close"] > df["ma_slow"]) &
                               (df["ma_slow"] > df["ma_slow"].shift(5))).values
    df["symbol"] = symbol
    df["sector"] = sector
    # trim to the requested window + one day of lookback for D-1 decisions
    start = pd.Timestamp(cfg.start).date()
    df = df[df["date"] >= start].dropna(subset=["atr", "ma_slow", "adv"]).reset_index(drop=True)
    return df if not df.empty else None


def run(symbols: list[str], sectors: dict[str, str], cfg: SimConfig, *,
        benchmark_symbol: str = "SPY", progress=lambda *_: None) -> SimResult:
    # 1) precompute per-symbol features
    feats: dict[str, pd.DataFrame] = {}
    for s in symbols:
        f = _build_features(s, sectors.get(s, "unknown"), cfg)
        if f is not None:
            feats[s] = f
    progress(f"features built for {len(feats)}/{len(symbols)} symbols")
    if not feats:
        raise RuntimeError("no symbols had enough data for the window")

    master = pd.concat(feats.values(), ignore_index=True)
    by_date = {d: g for d, g in master.groupby("date")}
    bars_by_symbol = {s: f.set_index("date") for s, f in feats.items()}

    # benchmark curve (SPY close)
    spy_bars = barsmod.get_bars(benchmark_symbol, "1d", start=cfg.start, end=cfg.end, adjust="split_div")
    if spy_bars.empty:
        raise RuntimeError(f"benchmark {benchmark_symbol!r} has no data — backfill it first")
    spy = spy_bars.set_index(spy_bars["ts"].dt.tz_convert("America/New_York").dt.date)["close"]

    trading_days = sorted(by_date.keys())
    cash = cfg.starting_capital
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    equity_curve: dict = {}

    def price_on(sym, d, col):
        try:
            return float(bars_by_symbol[sym].at[d, col])
        except KeyError:
            return None

    for i, d in enumerate(trading_days):
        prev = trading_days[i - 1] if i > 0 else None

        # --- EXITS (before entries) --------------------------------------
        for sym in list(positions.keys()):
            pos = positions[sym]
            row = bars_by_symbol[sym].loc[d] if d in bars_by_symbol[sym].index else None
            if row is None:
                continue
            direction = Direction(row["direction"])
            below_ma = row["close"] < row["ma_slow"]
            update_position_bar(pos, row["close"], row["high"], direction, below_ma)
            maybe_raise_trailing(pos, float(row["atr"]), cfg.exits)
            bench_ret = None
            if d in spy.index and pos.entry_date in spy.index:
                bench_ret = spy.loc[d] / spy.loc[pos.entry_date] - 1.0
            decision = evaluate_exit(
                pos, float(row["low"]), float(row["close"]),
                signal_direction=direction, expected_move=float(row["expected_move"]),
                params=cfg.exits, benchmark_return_since_entry=bench_ret)
            if decision is not None:
                fill = cfg.costs.exit_fill(decision.price)
                proceeds = pos.shares * fill - cfg.costs.commission(pos.shares)
                cash += proceeds
                pnl = pos.shares * (fill - pos.avg_cost) - cfg.costs.commission(pos.shares)
                trades.append(Trade(sym, pos.entry_date, d, pos.avg_cost, fill,
                                    pos.shares, pos.r_unit, pnl, decision.action))
                del positions[sym]

        # --- ENTRIES (signals from D-1, fill at D open) ------------------
        if prev is not None and prev in by_date:
            equity_now = cash + sum(
                p.shares * (price_on(s, d, "close") or p.avg_cost)
                for s, p in positions.items())
            slots = sizing.concurrent_slots(equity_now)
            cand = by_date[prev]
            shortlist = fn.build_shortlist(cand, cfg.funnel)
            shortlist += fn.select_etfs(cand)

            for sym in shortlist:
                # pyramid an existing winner, else open if a slot is free
                d1 = bars_by_symbol[sym].loc[prev] if prev in bars_by_symbol[sym].index else None
                open_px = price_on(sym, d, "open")
                if d1 is None or open_px is None:
                    continue
                entry = cfg.costs.entry_fill(open_px)
                stop = entry - float(d1["stop_distance"])
                if stop <= 0 or entry <= stop:
                    continue

                if sym in positions:
                    pos = positions[sym]
                    if not sizing.pyramid_allowed(pos.avg_cost, open_px, pos.adds_done):
                        continue
                    add_sh = sizing.position_size(equity_now, entry, stop, risk_pct=cfg.risk_pct,
                                                  slots=slots, settled_cash=cash, adv=float(d1["adv"]))
                    cost = add_sh * entry + cfg.costs.commission(add_sh)
                    if add_sh <= 0 or cost > cash:
                        continue
                    new_total = pos.shares + add_sh
                    pos.avg_cost = sizing.blend_avg_cost(pos.shares, pos.avg_cost, add_sh, entry)
                    pos.shares = new_total
                    pos.stop = sizing.raise_stop_for_combined_risk(new_total, pos.avg_cost,
                                                                   equity_now, pos.stop, risk_pct=cfg.risk_pct)
                    pos.adds_done += 1
                    cash -= cost
                elif len(positions) < slots:
                    sh = sizing.position_size(equity_now, entry, stop, risk_pct=cfg.risk_pct,
                                              slots=slots, settled_cash=cash, adv=float(d1["adv"]))
                    cost = sh * entry + cfg.costs.commission(sh)
                    if sh <= 0 or cost > cash:
                        continue
                    positions[sym] = Position(
                        symbol=sym, shares=sh, avg_cost=entry, stop=stop,
                        r_unit=(entry - stop), entry_date=d, high_water=entry)
                    cash -= cost

        # --- mark to market ----------------------------------------------
        mtm = cash + sum(p.shares * (price_on(s, d, "close") or p.avg_cost)
                         for s, p in positions.items())
        equity_curve[d] = mtm
        if i % 250 == 0:
            progress(f"  {d}: equity {mtm:,.0f}, {len(positions)} open, {len(trades)} closed")

    equity = pd.Series(equity_curve).sort_index()
    bench = spy.reindex(equity.index).ffill()
    bench_curve = cfg.starting_capital * (bench / bench.iloc[0]) if len(bench) else bench
    return SimResult(equity=equity, trades=trades, benchmark=bench_curve)
