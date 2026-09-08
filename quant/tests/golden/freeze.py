"""Phase 0 baseline freeze — the golden day the market-data refactor must not move.

Freezes, for SESSION_DATE, the inputs AND the outputs of the daily strategy so a
store/provider refactor can be proved to change neither (plan Phase 0):

  symbols.json              resolved golden symbol set + sector map (no DB needed)
  bars_<date>.parquet       the exact frames bars_fn feeds the engine (long form,
                            one 'symbol' column; UTC ts, store dtypes, row counts)
  recs_<date>.json          cycles.build_recommendations output, every field
  backtest_<date>.json      simulator metrics under SIM_CONFIG + the resolved config

Regenerate — each part in the environment its assertion runs in (build_recommendations
lives in backend/ and needs SQLAlchemy, which quant/ deliberately does not have):

    source quant/.venv/bin/activate && python -m quant.tests.golden.freeze
    docker compose exec -T backend python -m quant.tests.golden.freeze --parts recs
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quant import config
from quant.backtest import metrics, simulator
from quant.backtest.costs import CostModel
from quant.data import bars as qbars
from quant.data import store
from quant.engine.exits import ExitParams
from quant.engine.funnel import FunnelParams
from quant.engine.strategy import StrategyParams

SESSION_DATE = date(2026, 8, 29)

GOLDEN_DIR = Path(__file__).resolve().parent
SYMBOLS_JSON = GOLDEN_DIR / "symbols.json"
BARS_PARQUET = GOLDEN_DIR / f"bars_{SESSION_DATE}.parquet"
RECS_JSON = GOLDEN_DIR / f"recs_{SESSION_DATE}.json"
BACKTEST_JSON = GOLDEN_DIR / f"backtest_{SESSION_DATE}.json"

# The live signal_cycle shortlist knob (cycles.RECOMMENDED_FUNNEL, R0-9 G1
# baseline). Restated here so the golden is pinned to a value, not to an import
# from backend/ that quant/ cannot see.
GOLDEN_MIN_CONFIDENCE = 65.0
BACKTEST_START = "2024-01-01"


def json_default(obj):
    """Strict encoder hook: numpy scalars become their Python value and dates
    their ISO string. Everything else raises — a silent str() of a numpy bool is
    how a golden turns into "False" and stops comparing."""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"golden JSON cannot encode {type(obj).__name__}: {obj!r}")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True,
                               default=json_default) + "\n")


def sim_config() -> simulator.SimConfig:
    """The frozen BacktestConfig. Every knob is spelled out rather than left to a
    config.py default, so a default drifting shows up as a golden mismatch."""
    return simulator.SimConfig(
        start=BACKTEST_START,
        end=str(SESSION_DATE),
        starting_capital=2_000.0,
        adv_window=20,
        strategy=StrategyParams(),
        funnel=FunnelParams(min_confidence=GOLDEN_MIN_CONFIDENCE),
        exits=ExitParams(),
        costs=CostModel(),
        risk_pct=0.03,
        daily_loss_pause_pct=0.02,
        drawdown_halt_pct=0.15,
        halt_cooldown_sessions=21,
        membership_on=None,
        regime_ma=None,
    )


def config_dict(cfg: simulator.SimConfig) -> dict:
    d = asdict(cfg)
    d.pop("membership_on")
    return json.loads(json.dumps(d, default=json_default))


# --- symbol set ------------------------------------------------------------

def resolve_symbols() -> list[str]:
    """The signal_cycle analyzed universe for SESSION_DATE, DB-free: the top
    LIQUIDITY_TOP_PCT slice of that day's S&P 500 members plus the ETF whitelist.
    signal_cycle also unions open positions + the owner's watchlist; both need PG,
    so they are left out and the resolved list is frozen into symbols.json instead.
    Ranking reads the store truncated at SESSION_DATE, so a store that has grown
    since still resolves the same set."""
    from quant.data import universe

    def bars_reader(symbol: str) -> pd.DataFrame:
        return qbars._slice(store.read_bars(symbol, "daily"), None, SESSION_DATE)

    index_syms = sorted(universe.constituents_on(SESSION_DATE))
    liquid = set(universe.top_liquid(index_syms, bars_reader=bars_reader))
    return sorted(liquid | set(config.ETF_WHITELIST))


def freeze_symbols() -> list[str]:
    from quant.data import sectors as qsectors

    symbols = resolve_symbols()
    all_sectors = qsectors.load_sectors()
    payload = {
        "session_date": str(SESSION_DATE),
        "selection": ("top %.0f%% by %d-session average share volume of the S&P 500 "
                      "members as of session_date, plus ETF_WHITELIST"
                      % (config.LIQUIDITY_TOP_PCT * 100, config.LIQUIDITY_WINDOW)),
        "symbols": symbols,
        "sectors": {s: all_sectors.get(s, "unknown") for s in symbols},
    }
    _write_json(SYMBOLS_JSON, payload)
    return symbols


def load_symbols() -> tuple[list[str], dict[str, str]]:
    payload = json.loads(SYMBOLS_JSON.read_text())
    return payload["symbols"], payload["sectors"]


# --- frozen bars -----------------------------------------------------------

def freeze_bars(symbols: list[str]) -> pd.DataFrame:
    frames = []
    for sym in symbols:
        df = qbars.get_bars(sym, "1d", end=SESSION_DATE)
        if df.empty:
            continue
        df = df.copy()
        df.insert(0, "symbol", sym)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(BARS_PARQUET, compression=config.PARQUET_COMPRESSION, index=False)
    return out


def frozen_get_bars(frozen: pd.DataFrame | None = None):
    """A get_bars() stand-in reading the frozen parquet — the drop-in the golden
    assertions inject so the engine sees byte-identical input. Prices are already
    adjusted (they are get_bars output), so `adjust` is accepted and ignored;
    start/end go through bars._slice for identical windowing semantics."""
    if frozen is None:
        frozen = pd.read_parquet(BARS_PARQUET)
    by_symbol = {s: g.drop(columns=["symbol"]).reset_index(drop=True)
                 for s, g in frozen.groupby("symbol", sort=False)}
    empty = pd.DataFrame(columns=list(config.BAR_COLUMNS))

    def get_bars(symbol: str, timeframe: str = "1d", start=None, end=None, *,
                 adjust: str = "split_div", session: str = "regular") -> pd.DataFrame:
        if timeframe.lower() not in ("1d", "1day", "d", "daily"):
            raise ValueError(f"golden bars are daily only, got {timeframe!r}")
        df = by_symbol.get(symbol.upper())
        if df is None:
            return empty.copy()
        return qbars._slice(df.copy(), start, end)

    return get_bars


# --- goldens ---------------------------------------------------------------

def freeze_recs(symbols: list[str], sectors: dict[str, str]) -> list[dict]:
    from app.modules.simledger import cycles

    bars_fn = cycles.memoized_bars_fn(SESSION_DATE, get_bars=frozen_get_bars())
    recs = cycles.build_recommendations(symbols, SESSION_DATE, bars_fn=bars_fn)
    for r in recs:
        r["features"]["sector"] = sectors.get(r["symbol"], "unknown")
    recs = sorted(recs, key=lambda r: r["symbol"])
    _write_json(RECS_JSON, {"session_date": str(SESSION_DATE),
                            "min_confidence": GOLDEN_MIN_CONFIDENCE,
                            "count": len(recs),
                            "recommendations": recs})
    return recs


def run_backtest(symbols: list[str], sectors: dict[str, str],
                 cfg: simulator.SimConfig, *, get_bars) -> dict:
    """Run the simulator against an injected bar source. simulator reads bars
    through the module-level quant.data.bars.get_bars, so the frozen source is
    swapped in around the run rather than passed as an argument."""
    original = qbars.get_bars
    qbars.get_bars = get_bars
    try:
        result = simulator.run(symbols, sectors, cfg)
    finally:
        qbars.get_bars = original
    return metrics.summary(result.equity, result.trades, result.benchmark)


def freeze_backtest(symbols: list[str], sectors: dict[str, str]) -> dict:
    cfg = sim_config()
    summary = run_backtest(symbols, sectors, cfg, get_bars=frozen_get_bars())
    _write_json(BACKTEST_JSON, {"session_date": str(SESSION_DATE),
                                "config": config_dict(cfg),
                                "metrics": summary})
    return summary


PARTS = ("symbols", "bars", "recs", "backtest")


def main(parts: list[str]) -> None:
    if "symbols" in parts:
        freeze_symbols()
    symbols, sectors = load_symbols()
    print(f"symbols: {len(symbols)}")
    if "bars" in parts:
        frozen = freeze_bars(symbols)
        print(f"bars: {len(frozen)} rows, {frozen['symbol'].nunique()} symbols, "
              f"{BARS_PARQUET.stat().st_size / 1e6:.1f} MB")
    if "recs" in parts:
        recs = freeze_recs(symbols, sectors)
        print(f"recs: {len(recs)} rows, "
              f"{sum(1 for r in recs if r['shortlist_rank'])} shortlisted")
    if "backtest" in parts:
        summary = freeze_backtest(symbols, sectors)
        print(f"backtest: {summary['num_trades']} trades, "
              f"final equity {summary['final_equity']:,.2f}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="CAT Phase 0 golden baseline freeze")
    ap.add_argument("--parts", default="symbols,bars,backtest",
                    help=f"comma-separated subset of {','.join(PARTS)}")
    args = ap.parse_args()
    selected = [p.strip() for p in args.parts.split(",") if p.strip()]
    unknown = set(selected) - set(PARTS)
    if unknown:
        ap.error(f"unknown parts: {sorted(unknown)}")
    main(selected)
