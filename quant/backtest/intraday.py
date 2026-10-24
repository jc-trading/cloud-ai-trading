"""1min group simulator — one ``step`` per bar minute, shared by the live
intraday engine (P5c) and the 1m backtest.

A group's day is a ``GroupDayState`` advanced minute by minute in the fixed
P5 D-6 order: expire, fill pending, exits, state update, time exits, last-minute
flatten, intraday-loss pause, entry decisions, cash rounding. Decisions use the
bar-t close and fill at a later bar's open; a minute without a bar for a symbol
neither fills nor triggers anything for it (IEX printed nothing). Cash is kept
to 6 dp per fill and rounded to cents once a minute, the way the ledger's
Numeric(18,2) reads it back.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

import numpy as np
import pandas as pd

from quant.backtest.intraday_costs import IntradayCostModel
from quant.engine import sizing
from quant.engine.intraday_strategies import atr_window
from quant.engine.trade_rules import IntradayTradeSettings

_BAR_COLS = ("ts", "open", "high", "low", "close", "volume")
_MINUTE = pd.Timedelta(minutes=1)
LOSS_PAUSE = "intraday_loss_pause"


@dataclass
class OpenLot:
    symbol: str
    shares: float
    avg_cost: float
    stop: float
    target: float | None
    fill_bar_ts: pd.Timestamp
    r_unit: float
    high_water: float
    bars_held: int
    max_hold_minutes: int | None
    flatten_decide_ts: pd.Timestamp


@dataclass
class Pending:
    ref: str
    symbol: str
    side: str
    kind: str
    reason: str
    decided_bar_ts: pd.Timestamp
    expires_bar_ts: pd.Timestamp | None
    limit_price: float | None
    score: float | None
    equity_ref: float | None
    stop_distance: float | None
    target_distance: float | None
    max_hold_minutes: int | None


@dataclass
class GroupDayState:
    session_date: object
    cash: float
    lots: dict[str, OpenLot]
    pending: list[Pending]
    entries_today: dict[str, int]
    day_start_equity: float
    entries_paused_reason: str | None = None
    last_close: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    kind: str
    side: str
    symbol: str
    reason: str
    bar_ts: pd.Timestamp
    raw_price: float | None
    eff_price: float | None
    qty: float | None
    ref: str
    order_kind: str
    score: float | None
    r_unit: float | None = None


class DayContext:
    """One session's bars as the engine has seen them so far: the prior RTH
    sessions plus every minute added, never anything after the last minute."""

    def __init__(self, prior: dict[str, pd.DataFrame]):
        self._prior = {s: df.loc[:, list(_BAR_COLS)].reset_index(drop=True)
                       for s, df in prior.items()}
        self._prior_ns = {s: df["ts"].to_numpy(dtype="datetime64[ns]").astype("int64")
                          for s, df in self._prior.items()}
        self._prior_hlc = {s: df[["high", "low", "close"]].to_numpy(dtype="float64")
                           for s, df in self._prior.items()}
        self._bars: dict[str, dict[pd.Timestamp, dict]] = {}
        self._ns: dict[str, list[int]] = {}
        self._rows: dict[str, list[tuple]] = {}
        self._last: pd.Timestamp | None = None
        self._frames: dict[tuple[str, str], tuple[int, pd.DataFrame]] = {}
        self._atr: dict[tuple, float | None] = {}

    @classmethod
    def from_prior(cls, prior: dict[str, pd.DataFrame]) -> DayContext:
        return cls(prior)

    def add_minute(self, t: pd.Timestamp, bars: dict[str, dict]) -> None:
        if self._last is not None and t <= self._last:
            raise ValueError(f"minute {t} is not after {self._last}")
        self._last = t
        for sym, b in bars.items():
            self._bars.setdefault(sym, {})[t] = b
            self._ns.setdefault(sym, []).append(t.value)
            self._rows.setdefault(sym, []).append(
                (float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]),
                 float(b["volume"])))

    def bar(self, sym: str, t: pd.Timestamp) -> dict | None:
        return self._bars.get(sym, {}).get(t)

    def today(self, sym: str) -> pd.DataFrame:
        rows = self._rows.get(sym, [])
        cached = self._frames.get((sym, "today"))
        if cached is None or cached[0] != len(rows):
            vals = np.array(rows, dtype="float64").reshape(len(rows), 5)
            ts = np.array(self._ns.get(sym, []), dtype="int64").view("datetime64[ns]")
            df = pd.DataFrame({"ts": pd.DatetimeIndex(ts).tz_localize("UTC"),
                               **{c: vals[:, i] for i, c in enumerate(_BAR_COLS[1:])}})
            cached = (len(rows), df)
            self._frames[(sym, "today")] = cached
        return cached[1]

    def history(self, sym: str) -> pd.DataFrame:
        today = self.today(sym)
        cached = self._frames.get((sym, "history"))
        if cached is None or cached[0] != len(today):
            prior = self._prior.get(sym)
            df = today if prior is None or prior.empty else \
                pd.concat([prior, today], ignore_index=True)
            cached = (len(today), df)
            self._frames[(sym, "history")] = cached
        return cached[1]

    def atr(self, sym: str, t: pd.Timestamp, period: int) -> float | None:
        """``atr_window`` over the history rows at or before t; only the last
        4*period of them are materialised, the only rows it reads."""
        key = (sym, t, period)
        if key not in self._atr:
            n = 4 * period
            k = bisect_right(self._ns.get(sym, []), t.value)
            today = self._rows.get(sym, [])[max(0, k - n):k]
            need = n - len(today)
            parts = []
            if need > 0 and sym in self._prior_ns:
                pk = int(np.searchsorted(self._prior_ns[sym], t.value, side="right"))
                parts.append(self._prior_hlc[sym][max(0, pk - need):pk])
            if today:
                parts.append(np.array(today, dtype="float64")[:, 1:4])
            tail = np.concatenate(parts) if parts else np.empty((0, 3))
            self._atr[key] = atr_window(pd.DataFrame(tail, columns=["high", "low", "close"]),
                                        period)
        return self._atr[key]


def _cents(cash: float) -> float:
    return float(Decimal(str(round(cash, 6))).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _ref(side: str, symbol: str, t: pd.Timestamp, reason: str) -> str:
    return f"{side}:{symbol}:{t.tz_convert('UTC'):%Y%m%dT%H%MZ}:{reason}"


def _equity(state: GroupDayState) -> float:
    return state.cash + sum(lot.shares * state.last_close.get(sym, lot.avg_cost)
                            for sym, lot in state.lots.items())


def _cancel(p: Pending, t: pd.Timestamp, reason: str) -> Event:
    return Event("cancel", p.side, p.symbol, reason, t, None, None, None, p.ref, p.kind, p.score)


def step(state: GroupDayState, ctx: DayContext, t: pd.Timestamp, *, strategy,
         strategy_cfg, trade: IntradayTradeSettings, costs: IntradayCostModel,
         adv: dict[str, float | None], gating: str, score_floor: float | None, watched,
         shortlist, rth: tuple[pd.Timestamp, pd.Timestamp], risk_pct: float,
         mode: str) -> list[Event]:
    """Advance one group by bar minute t (P5 D-6 A-I) and return what happened.
    ``gating`` is "ok" or the reason entries are blocked; ``mode`` "catchup"
    replays exits but never decides an entry; ``shortlist`` is the ranked
    symbol order and ``watched`` the symbols whose bars the engine receives."""
    events: list[Event] = []
    rth_open, rth_close = rth
    flatten_decide = rth_close - (trade.flatten_minutes_before_close + 1) * _MINUTE

    def sell(sym: str, lot: OpenLot, raw: float, reason: str, order_kind: str, ref: str) -> None:
        eff = costs.exit_fill(raw, adv.get(sym))
        state.cash = round(state.cash + lot.shares * eff - costs.commission(lot.shares), 6)
        events.append(Event("fill", "sell", sym, reason, t, raw, round(eff, 6), lot.shares, ref,
                            order_kind, None, lot.r_unit))
        del state.lots[sym]

    def drop_sells(sym: str) -> None:
        for p in [p for p in state.pending if p.side == "sell" and p.symbol == sym]:
            state.pending.remove(p)
            events.append(_cancel(p, t, "lot closed"))

    # A expire
    for p in [p for p in state.pending if p.expires_bar_ts is not None and p.expires_bar_ts < t]:
        state.pending.remove(p)
        events.append(Event("expire", p.side, p.symbol, "ttl", t, None, None, None, p.ref,
                            p.kind, p.score))

    # B fill pending orders decided on an earlier bar
    inside_bar: set[str] = set()
    for p in sorted([p for p in state.pending if p.decided_bar_ts < t],
                    key=lambda p: (p.decided_bar_ts, p.side != "sell", p.symbol)):
        b = ctx.bar(p.symbol, t)
        if b is None:
            continue
        if p.side == "sell":
            state.pending.remove(p)
            lot = state.lots.get(p.symbol)
            if lot is not None:
                sell(p.symbol, lot, float(b["open"]), p.reason, p.kind, p.ref)
            continue
        if gating != "ok":
            state.pending.remove(p)
            events.append(_cancel(p, t, gating if gating.startswith("blocked:")
                                  else f"blocked: {gating}"))
            continue
        if p.kind == "limit":
            if not float(b["low"]) <= p.limit_price:
                continue
            raw = min(float(b["open"]), p.limit_price)
            if raw < float(b["open"]):
                inside_bar.add(p.symbol)
        else:
            raw = float(b["open"])
        slots = sizing.concurrent_slots(p.equity_ref)
        if len(state.lots) >= slots:
            state.pending.remove(p)
            events.append(_cancel(p, t, "no slot"))
            continue
        a = adv.get(p.symbol)
        eff = costs.entry_fill(raw, a)
        stop = raw - p.stop_distance
        if stop <= 0:
            state.pending.remove(p)
            events.append(_cancel(p, t, "invalid stop"))
            continue
        qty = math.floor(sizing.position_size(p.equity_ref, eff, stop, risk_pct=risk_pct,
                                              slots=slots, settled_cash=state.cash,
                                              adv=a) * 1e6) / 1e6
        if qty <= 0 or qty * eff > state.cash + 1e-9:
            state.pending.remove(p)
            events.append(_cancel(p, t, "insufficient cash"))
            continue
        lot = OpenLot(p.symbol, qty, round(eff, 6), round(stop, 6),
                      round(raw + p.target_distance, 6) if p.target_distance is not None else None,
                      t, round(eff - stop, 6), round(eff, 6), 0, p.max_hold_minutes,
                      flatten_decide)
        state.lots[p.symbol] = lot
        state.cash = round(state.cash - qty * eff, 6)
        state.entries_today[p.symbol] = state.entries_today.get(p.symbol, 0) + 1
        state.pending.remove(p)
        events.append(Event("fill", "buy", p.symbol, "entry", t, raw, lot.avg_cost, lot.shares,
                            p.ref, p.kind, p.score, lot.r_unit))

    # C resting stop / target exits on symbols with a bar at t
    for sym in sorted(state.lots):
        b = ctx.bar(sym, t)
        if b is None:
            continue
        lot = state.lots[sym]
        o, h, lo = float(b["open"]), float(b["high"]), float(b["low"])
        tgt = lot.target
        exit_ = None
        # a limit filled inside the bar came after its open (and maybe its high)
        if sym in inside_bar:
            if lo <= lot.stop:
                exit_ = (lot.stop, "hard_stop", "stop")
        elif o <= lot.stop:
            exit_ = (o, "hard_stop", "stop")
        elif tgt is not None and o >= tgt:
            exit_ = (o, "take_profit", "target")
        elif lo <= lot.stop:
            exit_ = (lot.stop, "hard_stop", "stop")
        elif tgt is not None and h >= tgt:
            exit_ = (tgt, "take_profit", "target")
        if exit_ is not None:
            raw, reason, kind = exit_
            sell(sym, lot, raw, reason, kind, _ref("sell", sym, t, reason))
            drop_sells(sym)

    # D state update (wall-clock minutes)
    for sym, lot in state.lots.items():
        lot.bars_held += 1
        b = ctx.bar(sym, t)
        if b is not None:
            lot.high_water = round(max(lot.high_water, float(b["high"]), float(b["close"])), 6)
    for sym in set(state.lots) | set(watched):
        b = ctx.bar(sym, t)
        if b is not None:
            state.last_close[sym] = float(b["close"])

    # E time exits decided on the t close
    selling = {p.symbol for p in state.pending if p.side == "sell"}
    for sym in sorted(state.lots):
        if sym in selling:
            continue
        lot = state.lots[sym]
        if lot.max_hold_minutes is not None and lot.bars_held >= lot.max_hold_minutes:
            reason = "max_hold"
        elif t >= lot.flatten_decide_ts:
            reason = "flatten"
        else:
            continue
        p = Pending(_ref("sell", sym, t, reason), sym, "sell", "market", reason, t, None, None,
                    None, None, None, None, None)
        state.pending.append(p)
        events.append(Event("order", "sell", sym, reason, t, None, None, lot.shares, p.ref,
                            "market", None))

    # F last RTH minute: anything still open closes at its last close
    if t == rth_close - _MINUTE:
        for sym in sorted(state.lots):
            lot = state.lots[sym]
            sell(sym, lot, state.last_close.get(sym, lot.avg_cost), "flatten_last_close",
                 "market", _ref("sell", sym, t, "flatten_last_close"))
            drop_sells(sym)

    # G intraday loss pause
    if state.entries_paused_reason is None and \
            _equity(state) <= state.day_start_equity * (1.0 - trade.intraday_loss_pause_pct):
        state.entries_paused_reason = LOSS_PAUSE
        for p in [p for p in state.pending if p.side == "buy"]:
            state.pending.remove(p)
            events.append(_cancel(p, t, f"blocked: {LOSS_PAUSE}"))

    # H entry decisions on the t close
    ttl = trade.entry_ttl_minutes * _MINUTE
    if gating == "ok" and state.entries_paused_reason is None and mode == "live" \
            and strategy is not None and t < min(
                flatten_decide - ttl,
                rth_open + strategy_cfg.signal.entry_window_minutes * _MINUTE):
        equity = _equity(state)
        slots = sizing.concurrent_slots(equity)
        buying = {p.symbol for p in state.pending if p.side == "buy"}
        watched_set = set(watched)
        for sym in shortlist:
            if sym not in watched_set:
                continue
            b = ctx.bar(sym, t)
            if b is None:
                continue
            if sym in state.lots or sym in buying or \
                    state.entries_today.get(sym, 0) >= trade.max_entries_per_symbol_day:
                continue
            if len(state.lots) + len(buying) >= slots:
                continue
            score = strategy(ctx.today(sym), t, rth_open,
                             ctx.atr(sym, t, strategy_cfg.signal.atr_period), strategy_cfg.signal)
            if score is None or (score_floor is not None and score < score_floor):
                continue
            close_t = float(b["close"])
            atr = ctx.atr(sym, t, trade.atr_period)
            if trade.stop_pct is not None:
                stop_distance = trade.stop_pct * close_t
            elif trade.stop_atr_mult is not None and atr is not None:
                stop_distance = trade.stop_atr_mult * atr
            else:
                continue
            if stop_distance <= 0:
                continue
            if trade.take_profit_pct is not None:
                target_distance = trade.take_profit_pct * close_t
            elif trade.take_profit_atr_mult is not None and atr is not None:
                target_distance = trade.take_profit_atr_mult * atr
            else:
                target_distance = None
            limit = trade.entry_limit_offset_pct
            p = Pending(_ref("buy", sym, t, "entry"), sym, "buy",
                        "limit" if limit is not None else "market", "entry", t, t + ttl,
                        round(close_t * (1.0 + limit), 4) if limit is not None else None,
                        score, round(equity, 2), stop_distance, target_distance,
                        trade.max_hold_minutes)
            state.pending.append(p)
            buying.add(sym)
            events.append(Event("order", "buy", sym, "entry", t, None, None, None, p.ref, p.kind,
                                score))

    # I cash as the ledger stores it
    state.cash = _cents(state.cash)
    return events


def run_day(state: GroupDayState, ctx: DayContext, inputs: Iterable, **kw) -> list[Event]:
    """Feed (t, bars, watched, gating, mode) minutes through ``step`` in order."""
    events: list[Event] = []
    for t, bars, watched, gating, mode in inputs:
        ctx.add_minute(t, bars)
        events += step(state, ctx, t, watched=watched, gating=gating, mode=mode, **kw)
    return events
