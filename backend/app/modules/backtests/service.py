"""Backtest runs (P3b): request validation, the config snapshot a run is
queued with, and a group's live equity / closed trades in the backtest shape
so the compare view scores both with the same ``metrics.payload``."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quant import config as qconfig
from quant.backtest.calibration import score_threshold
from quant.backtest.costs import CostModel
from quant.backtest.intraday_costs import IntradayCostModel
from quant.backtest.metrics import Trade
from quant.backtest.walkforward import walk_forward_windows
from quant.data import calendar as qcal
from quant.data import screen as qscreen
from quant.engine.screener import ScreenerSpecError, parse_spec, required_lookback
from quant.engine.strategies import StrategyParamsError, validate_grid
from quant.engine.trade_rules import TradeSettingsError, parse_trade_settings

from app.modules.backtests.models import KINDS, BacktestRun
from app.modules.simledger import cycles
from app.modules.simledger.models import (
    AccountSnapshot, SimAccount, SimFill, SimOrder, SimPosition,
)
from app.modules.simledger.settings import effective_settings
from app.modules.strategy_groups.models import StrategyGroup
from app.modules.strategy_groups.service import calibration_problem

BACKTEST_MIN_START = date(2016, 1, 1)
BACKTEST_MAX_ACTIVE = 3
SINGLE_MIN_SESSIONS = 120
INTRADAY_SINGLE_MIN_SESSIONS = 5
PLATFORM_KEYS = ("per_trade_risk_pct", "daily_loss_pause_pct", "portfolio_drawdown_halt_pct",
                 "min_confidence")
_ET = ZoneInfo("America/New_York")


class RunInvalid(ValueError):
    pass


class RunConflict(Exception):
    pass


def config_core(group: StrategyGroup) -> dict:
    return {"strategy_key": group.strategy_key, "params": group.params_json,
            "screener": group.screener_json,
            "trade_settings_base": {k: v for k, v in group.trade_settings_json.items()
                                    if k not in ("entry_prob_min", "calibration_run_id")},
            "timeframe": group.timeframe, "market": group.market}


def config_hash(core: dict) -> str:
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def engine_fingerprint() -> str:
    root = qconfig.REPO_ROOT / "quant"
    paths = [*root.glob("engine/*.py"), *root.glob("backtest/*.py"),
             root / "research" / "group_backtest.py", root / "data" / "screen.py"]
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        h.update(p.read_bytes())
    return h.hexdigest()


async def build_snapshot(db: AsyncSession, group: StrategyGroup, *, kind: str, is_years: int,
                         oos_years: int, grid: dict | None, trade=None, run_spec=None) -> dict:
    from quant.research.sensitivity import _store_latest_session

    account = await db.get(SimAccount, group.sim_account_id)
    eff = await effective_settings(db)
    snap = {
        **config_core(group),
        "group": {"id": str(group.id), "name": group.name},
        "starting_capital": float(account.starting_capital),
        "platform": {k: float(getattr(eff, k)) for k in PLATFORM_KEYS},
        "costs": asdict(CostModel()),
        "engine_fingerprint": await asyncio.to_thread(engine_fingerprint),
        "data_latest_session": await asyncio.to_thread(_store_latest_session),
        "run": {"kind": kind, "is_years": is_years, "oos_years": oos_years,
                "grid": None if grid is None else {k: list(v) for k, v in grid.items()}},
    }
    if group.timeframe == "1m":
        snap["costs"] = asdict(IntradayCostModel())
        snap["run"] = run_spec
    if trade is not None and trade.entry_prob_min is not None:
        rid = trade.calibration_run_id
        run = await db.get(BacktestRun, UUID(rid))
        problem = calibration_problem(run)
        if problem is not None:
            raise RunConflict(f"calibration run {rid} unusable: {problem}")
        floor = score_threshold(run.calibration_json, trade.entry_prob_min)
        if floor is None:
            raise RunConflict(f"calibration run {rid}: no calibrated bucket reaches "
                              f"p={trade.entry_prob_min}")
        snap["calibration_ref"] = {"run_id": rid, "entry_prob_min": trade.entry_prob_min,
                                   "blocks": run.calibration_json["blocks"],
                                   "score_floor": floor}
    return snap


def _intraday_run_spec(group: StrategyGroup, kind: str, start: date, end: date, *,
                       is_years, oos_years, is_sessions, oos_sessions, grid) -> dict:
    """Validate a 1m group's run request and return its snapshot ``run``:
    sessions instead of years, and no start before the 1min backfill horizon."""
    if is_years is not None or oos_years is not None:
        raise RunInvalid("is_years / oos_years do not apply to 1m groups — "
                         "use is_sessions / oos_sessions")
    floor = (pd.Timestamp(cycles.now_et().date())
             - pd.DateOffset(years=qconfig.INTRADAY_BACKFILL_YEARS_1M)).date()
    if start < floor:
        raise RunInvalid(f"1m runs: start must be >= {floor}")
    if kind == "single":
        if grid is not None:
            raise RunInvalid("grid applies to walk_forward runs only")
        n = len(qcal.sessions_in_range(start, end))
        if n < INTRADAY_SINGLE_MIN_SESSIONS:
            raise RunInvalid(f"a 1m single run needs >= {INTRADAY_SINGLE_MIN_SESSIONS} "
                             f"sessions, got {n}")
        return {"kind": kind, "is_sessions": None, "oos_sessions": None, "grid": None}
    is_sessions = 60 if is_sessions is None else is_sessions
    oos_sessions = 20 if oos_sessions is None else oos_sessions
    if not 20 <= is_sessions <= 250:
        raise RunInvalid("is_sessions must be 20-250")
    if not 10 <= oos_sessions <= 120:
        raise RunInvalid("oos_sessions must be 10-120")
    from quant.research.intraday_backtest import session_windows

    if not session_windows(qcal.sessions_in_range(start, end), is_sessions, oos_sessions):
        raise RunInvalid(f"no walk-forward window fits {start}..{end} "
                         f"(IS {is_sessions} + OOS {oos_sessions} sessions)")
    try:
        grid = validate_grid(group.strategy_key, grid)
    except StrategyParamsError as e:
        raise RunInvalid(str(e)) from None
    return {"kind": kind, "is_sessions": is_sessions, "oos_sessions": oos_sessions,
            "grid": {k: list(v) for k, v in grid.items()}}


async def create_run(db: AsyncSession, *, group: StrategyGroup, kind: str, start: date,
                     end: date, is_years=None, oos_years=None, is_sessions=None,
                     oos_sessions=None, grid=None, user_id=None) -> BacktestRun:
    """Validate a backtest request (RunInvalid -> 422, RunConflict -> 409) and
    insert it as ``queued`` with its config snapshot."""
    if kind not in KINDS:
        raise RunInvalid(f"kind must be one of {', '.join(KINDS)}")
    if start < BACKTEST_MIN_START:
        raise RunInvalid(f"start must be >= {BACKTEST_MIN_START}")
    last = qcal.previous_session(cycles.now_et().date())
    if end > last:
        raise RunInvalid(f"end must be <= {last} (the last closed session)")
    if not start < end:
        raise RunInvalid("start must be before end")
    if group.timeframe != "1m":
        if is_sessions is not None or oos_sessions is not None:
            raise RunInvalid("is_sessions / oos_sessions apply to 1m groups only")
        is_years = 3 if is_years is None else is_years
        oos_years = 1 if oos_years is None else oos_years
    run_spec = None
    if group.timeframe == "1m":
        run_spec = _intraday_run_spec(group, kind, start, end, is_years=is_years,
                                      oos_years=oos_years, is_sessions=is_sessions,
                                      oos_sessions=oos_sessions, grid=grid)
    elif kind == "single":
        if grid is not None:
            raise RunInvalid("grid applies to walk_forward runs only")
        n = len(qcal.sessions_in_range(start, end))
        if n < SINGLE_MIN_SESSIONS:
            raise RunInvalid(f"a single run needs >= {SINGLE_MIN_SESSIONS} sessions, got {n}")
    else:
        if not 1 <= is_years <= 5:
            raise RunInvalid("is_years must be 1-5")
        if not 1 <= oos_years <= 2:
            raise RunInvalid("oos_years must be 1-2")
        if not walk_forward_windows(start, end, is_years=is_years, oos_years=oos_years,
                                    step_years=oos_years):
            raise RunInvalid(f"no walk-forward window fits {start}..{end} "
                             f"(IS {is_years}y + OOS {oos_years}y)")
        try:
            grid = validate_grid(group.strategy_key, grid)
        except StrategyParamsError as e:
            raise RunInvalid(str(e)) from None
    try:
        trade = parse_trade_settings(group.trade_settings_json, timeframe=group.timeframe)
    except TradeSettingsError as e:
        raise RunInvalid(str(e)) from None
    try:
        spec = parse_spec(group.screener_json)
    except ScreenerSpecError as e:
        raise RunInvalid(str(e)) from None
    if spec is not None:
        earliest = await asyncio.to_thread(qscreen.earliest_start, spec)
        if earliest is None:
            raise RunInvalid("no panel data")
        if start < earliest:
            raise RunInvalid(f"start must be >= {earliest} (panel history + "
                             f"{required_lookback(spec)}-session lookback)")
    active = (await db.execute(select(func.count()).select_from(BacktestRun).where(
        BacktestRun.status.in_(("queued", "running"))))).scalar_one()
    if active >= BACKTEST_MAX_ACTIVE:
        raise RunConflict(f"{active} backtests already queued or running "
                          f"(max {BACKTEST_MAX_ACTIVE})")
    snapshot = await build_snapshot(db, group, kind=kind, is_years=is_years,
                                    oos_years=oos_years, grid=grid, trade=trade,
                                    run_spec=run_spec)
    run = BacktestRun(group_id=group.id, kind=kind, status="queued",
                      config_snapshot=snapshot, config_hash=config_hash(config_core(group)),
                      data_start=start, data_end=end,
                      universe_label=("pit_sp500" if group.screener_json == {}
                                      else "screener_current_universe"),
                      survivorship_biased=group.screener_json != {}, created_by=user_id)
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def live_series(db: AsyncSession, group: StrategyGroup, start: date | None = None
                      ) -> tuple[pd.Series, list[Trade]]:
    """The group account's daily equity (snapshots) and one Trade per closed
    position: shares = its buy fills, exit price = its sell fill, pnl net of
    every commission. Fills reach a position through ``sim_orders``."""
    snaps = select(AccountSnapshot.snapshot_date, AccountSnapshot.equity).where(
        AccountSnapshot.account_id == group.sim_account_id)
    if start is not None:
        snaps = snaps.where(AccountSnapshot.snapshot_date >= start)
    rows = (await db.execute(snaps.order_by(AccountSnapshot.snapshot_date))).all()
    equity = pd.Series([float(e) for _, e in rows], index=[d for d, _ in rows],
                       dtype="float64")

    positions = (await db.execute(
        select(SimPosition).where(SimPosition.account_id == group.sim_account_id,
                                  SimPosition.status == "closed")
        .order_by(SimPosition.closed_at, SimPosition.id))).scalars().all()
    fills: dict = {}
    if positions:
        for pid, side, price, qty, commission in (await db.execute(
                select(SimOrder.position_id, SimOrder.side, SimFill.price, SimFill.qty,
                       SimFill.commission)
                .join(SimOrder, SimOrder.id == SimFill.order_id)
                .where(SimOrder.position_id.in_([p.id for p in positions]),
                       SimOrder.status == "filled"))).all():
            fills.setdefault(pid, []).append((side, float(price), float(qty), float(commission)))
    trades = []
    for pos in positions:
        mine = fills.get(pos.id, [])
        buys = [(p, q) for s, p, q, _ in mine if s == "buy"]
        sells = [(p, q) for s, p, q, _ in mine if s == "sell"]
        if not sells or pos.closed_at is None:
            continue
        exit_date = pos.closed_at.astimezone(_ET).date()
        if start is not None and exit_date < start:
            continue
        sold = sum(q for _, q in sells)
        if sold <= 0:
            continue
        trades.append(Trade(
            symbol=pos.symbol, entry_date=pos.entry_date, exit_date=exit_date,
            entry_price=float(pos.avg_cost), exit_price=sum(p * q for p, q in sells) / sold,
            shares=sum(q for _, q in buys), r_unit=float(pos.r_unit),
            pnl=(sum(p * q for p, q in sells) - sum(p * q for p, q in buys)
                 - sum(c for *_, c in mine)),
            exit_reason=pos.close_reason or ""))
    return equity, trades
