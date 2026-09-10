"""End-of-day SIP correction for the intraday store — 方案 Phase 8.

Once an ET trading day is over, every 1-minute file the IEX WebSocket wrote
during the session is re-fetched whole from SIP and overwritten (the file's
provenance flips ``alpaca:iex`` -> ``alpaca:sip``), and the same day's 1hour
bars are synced alongside. Before an overwrite the stored IEX day is compared
minute-by-minute against the SIP truth and the numbers land in
``market_data_files.meta`` — that comparison is the quantitative answer to "is
the free IEX feed good enough / when must we pay for consolidated data", and
the same code validates any future provider's overlap before its bars are
allowed to join the store.

A day is judged on its REGULAR session only: measured over the whole
04:00-20:00 window, a thin name legitimately has no extended-hours bars at all,
so full-window completeness reads liquidity rather than data loss (2026-09-04:
every symbol was 390/390 in RTH while the window ratio ran 0.38-0.99). The
window ratio stays in ``meta.completeness`` as the feed diagnostic; ``partial``
keys on ``meta.rth_completeness``.

Fail-closed on the same judgement as ``signal_cycle``'s A2 rule: if more than
``FAIL_CLOSED_RATIO`` of the symbols come back empty or error, NOTHING is
overwritten, the day's rows are marked ``stale`` and the caller alerts.

Coordination with the live stream needs no pause protocol: the writer stops
once the ET date rolls, and ``write_bars`` takes the same per-file lock anyway.

Run manually for a past day inside the container:

    python -m quant.data.eod_correction --date 2026-09-04
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from quant import config
from quant.data import calendar, store
from quant.data.providers import get_historical
from quant.data.registry import resolve_stream_symbols
from quant.data.stream import SESSION_CLOSE_ET, SESSION_OPEN_ET

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
TIMEFRAME = "1min"
HOUR_TIMEFRAME = "1hour"
# 拍板 2026-09-07: extended hours are part of the session, and a half day's
# after-hours tape stops at 17:00 ET instead of 20:00 (defined in calendar)
EARLY_CLOSE_END_ET = calendar.EARLY_CLOSE_END_ET
# A2 fail-closed: the same 20% judgement signal_cycle uses on bar sync
FAIL_CLOSED_RATIO = 0.2
MIN_RTH_COMPLETENESS = 0.99
# how far back last_completed_session() may walk for a closed session
_MAX_LOOKBACK_SESSIONS = 10


def _ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def session_window(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The [open, close) session window of one ET trading day, in UTC — the same
    04:00-20:00 window the WS writer and the REST backfill use, cut short to
    17:00 ET on early-close days. Thin alias over the one calendar definition."""
    return calendar.session_bounds(day)


def expected_minutes(day: date) -> int:
    open_ts, close_ts = session_window(day)
    return int((close_ts - open_ts).total_seconds() // 60)


def rth_expected_minutes(day: date) -> int:
    open_ts, close_ts = calendar.rth_bounds(day)
    return int((close_ts - open_ts).total_seconds() // 60)


def rth_rows(df: pd.DataFrame, day: date) -> int:
    open_ts, close_ts = calendar.rth_bounds(day)
    return int(((df["ts"] >= open_ts) & (df["ts"] < close_ts)).sum())


def last_completed_session(now=None) -> date | None:
    """Newest ET trading day whose session window closed at least the free-tier
    SIP delay ago; None if the calendar has none in reach."""
    now_ts = _ts(now or datetime.now(timezone.utc))
    day = now_ts.tz_convert(_ET).date()
    for _ in range(_MAX_LOOKBACK_SESSIONS):
        if calendar.is_trading_day(day):
            _, close_ts = session_window(day)
            if now_ts >= close_ts + pd.Timedelta(minutes=config.SIP_DELAY_MINUTES):
                return day
        day = calendar.previous_session(day)
    return None


def compare_feeds(iex: pd.DataFrame, sip: pd.DataFrame) -> dict:
    """IEX vs SIP for one stored day: close difference in bps (median and max
    over the minutes both feeds carry), the IEX/SIP volume ratio, and how many
    SIP minutes IEX never saw."""
    iex, sip = store.normalize(iex), store.normalize(sip)
    merged = iex.merge(sip, on="ts", suffixes=("_iex", "_sip"))
    bps = pd.Series(dtype="float64")
    if not merged.empty:
        ref = pd.to_numeric(merged["close_sip"], errors="coerce").abs()
        left = pd.to_numeric(merged["close_iex"], errors="coerce")
        bps = ((left - ref).abs() / ref.where(ref > 0) * 1e4).dropna()
    vol_iex = float(pd.to_numeric(iex["volume"], errors="coerce").fillna(0).sum())
    vol_sip = float(pd.to_numeric(sip["volume"], errors="coerce").fillna(0).sum())
    return {
        "minutes_iex": int(len(iex)),
        "minutes_sip": int(len(sip)),
        "minutes_common": int(len(merged)),
        "close_bps_median": round(float(bps.median()), 4) if not bps.empty else None,
        "close_bps_max": round(float(bps.max()), 4) if not bps.empty else None,
        "volume_ratio": round(vol_iex / vol_sip, 6) if vol_sip > 0 else None,
        "iex_missing_minutes": int(len(set(sip["ts"]) - set(iex["ts"]))),
    }


@dataclass
class SymbolResult:
    symbol: str
    status: str
    rows: int = 0
    hour_rows: int = 0
    completeness: float | None = None
    rth_completeness: float | None = None
    comparison: dict | None = None
    error: str | None = None


@dataclass
class CorrectionReport:
    day: date | None
    period_key: str | None = None
    expected: int = 0
    symbols: list[str] = field(default_factory=list)
    results: list[SymbolResult] = field(default_factory=list)
    already_corrected: list[str] = field(default_factory=list)
    hour_healed: list[str] = field(default_factory=list)
    fail_closed: str | None = None
    skipped: str | None = None

    def _of(self, status: str) -> list[str]:
        return [r.symbol for r in self.results if r.status == status]

    @property
    def corrected(self) -> list[str]:
        return self._of("ok")

    @property
    def partial(self) -> list[str]:
        return self._of("partial")

    @property
    def stale(self) -> list[str]:
        return self._of("stale")

    @property
    def rows(self) -> int:
        return sum(r.rows for r in self.results)

    def summary(self) -> str:
        if self.skipped:
            return f"skipped: {self.skipped}"
        head = (f"{self.day} corrected={len(self.corrected)} "
                f"partial={len(self.partial)} stale={len(self.stale)} "
                f"skipped={len(self.already_corrected)} "
                f"hour_healed={len(self.hour_healed)} "
                f"rows={self.rows}/{self.expected * len(self.symbols)}")
        return f"fail-closed: {self.fail_closed} ({head})" if self.fail_closed else head


def resolve_symbols(registry, period_key: str) -> list[str]:
    """The stream's subscription set plus every symbol that already has a 1min
    file for that day — a name unsubscribed mid-session still owns a half IEX
    day that has to be corrected."""
    return resolve_stream_symbols(
        registry, extra=registry.symbols_with_file(TIMEFRAME, period_key))[0]


def _liquid_symbols(registry) -> set[str]:
    """Names a partial day actually matters for: what the 对照账户 holds plus the
    deliberately configured stream set. A watchlist name can be illiquid by
    choice and must not page anyone at 21:30."""
    liquid = {row.symbol.upper() for row in registry.stream_symbols()}
    account = registry.system_account()
    if account is not None:
        liquid |= {s.upper() for s in registry.open_position_symbols(account.account_id)}
    return liquid


def _fetch_day(provider, symbol: str, timeframe: str, open_ts, close_ts,
               now) -> pd.DataFrame:
    df = provider.fetch_bars(symbol, timeframe, open_ts.to_pydatetime(),
                             close_ts.to_pydatetime(), now=now)
    df = store.normalize(df)
    if df.empty:
        return df
    return df[(df["ts"] >= open_ts) & (df["ts"] < close_ts)].reset_index(drop=True)


def hour_period_key(day: date) -> str:
    open_ts, _ = session_window(day)
    return store.period_key(open_ts, HOUR_TIMEFRAME)


def hour_synced(registry, symbol: str, day: date) -> bool:
    """Whether the month's 1hour file already covers this session — the month
    is written in one atomic call per day, so a ``last_ts`` that reaches into
    the day proves the sync landed."""
    open_ts, _ = session_window(day)
    row = registry.get_row(symbol, HOUR_TIMEFRAME, hour_period_key(day))
    return (row is not None and row.last_ts is not None
            and _ts(row.last_ts) >= open_ts)


def sync_hours(symbol: str, day: date, *, provider, registry,
               now) -> tuple[int, str | None]:
    """Merge the day's 1hour bars into the month file. Returns (rows, error)."""
    open_ts, close_ts = session_window(day)
    try:
        hours = _fetch_day(provider, symbol, HOUR_TIMEFRAME, open_ts, close_ts, now)
        if hours.empty:
            return 0, None
        rows = store.write_frame(symbol, HOUR_TIMEFRAME, hours,
                                 provider=provider.provider_key, registry=registry)
        return rows, None
    except Exception as exc:
        logger.warning("%s %s: 1hour sync failed", symbol, day, exc_info=True)
        return 0, f"{type(exc).__name__}: {exc}"


def run(day: date | str | None = None, *, now=None, provider=None, registry=None,
        symbols: list[str] | None = None) -> CorrectionReport:
    """Re-source one ET trading day's intraday bars from SIP.

    Two passes on purpose: every symbol is fetched before anything is written,
    so the >20% fail-closed rule can abort the day without having already
    overwritten half the files.
    """
    now_ts = _ts(now or datetime.now(timezone.utc))
    if registry is None:
        from quant.data import registry as registry_module

        registry = registry_module
    provider = provider or get_historical(config.PROVIDER_HISTORICAL)

    if day is None:
        day = last_completed_session(now_ts)
        if day is None:
            return CorrectionReport(day=None, skipped="no completed session")
    else:
        day = pd.Timestamp(day).date()
        if not calendar.is_trading_day(day):
            return CorrectionReport(day=day, skipped=f"{day} is not a session")
        _, close_ts = session_window(day)
        if now_ts < close_ts + pd.Timedelta(minutes=config.SIP_DELAY_MINUTES):
            return CorrectionReport(day=day, skipped=f"{day} session not closed yet")

    open_ts, close_ts = session_window(day)
    period_key = day.isoformat()
    expected = expected_minutes(day)
    wanted = [s.upper() for s in symbols] if symbols else resolve_symbols(registry, period_key)
    report = CorrectionReport(day=day, period_key=period_key, expected=expected,
                              symbols=wanted)
    if not wanted:
        report.skipped = "no symbols to correct"
        return report

    pending, hour_only = [], []
    for symbol in wanted:
        row = registry.get_row(symbol, TIMEFRAME, period_key)
        minute_done = (row is not None and row.provider == provider.provider_key
                       and row.status == "ok")
        if not minute_done:
            pending.append(symbol)
        elif hour_synced(registry, symbol, day):
            logger.info("%s %s: already corrected from %s — skipping",
                        symbol, period_key, row.provider)
            report.already_corrected.append(symbol)
        else:
            logger.info("%s %s: 1min already corrected but %s is missing the day "
                        "— syncing 1hour only", symbol, period_key,
                        hour_period_key(day))
            hour_only.append(symbol)
    # the crontab fires every calendar night, so a weekend/holiday run resolves
    # the SAME last session; re-fetching it would spend the REST budget and
    # (worse) overwrite the feed comparison this day was measured with
    if not pending and not hour_only:
        report.skipped = "already corrected"
        return report

    fetched: dict[str, pd.DataFrame] = {}
    failures: list[SymbolResult] = []
    for symbol in pending:
        try:
            df = _fetch_day(provider, symbol, TIMEFRAME, open_ts, close_ts, now_ts)
        except Exception as exc:
            logger.warning("%s %s: SIP 1min fetch failed", symbol, period_key,
                           exc_info=True)
            failures.append(SymbolResult(symbol=symbol, status="stale",
                                         error=f"{type(exc).__name__}: {exc}"))
            continue
        if df.empty:
            logger.warning("%s %s: SIP returned no bars — keeping the stored file",
                           symbol, period_key)
            failures.append(SymbolResult(symbol=symbol, status="stale",
                                         error="empty SIP page"))
            continue
        fetched[symbol] = df

    if len(failures) > FAIL_CLOSED_RATIO * len(pending):
        report.fail_closed = (f"SIP fetch failed for {len(failures)}/{len(pending)} "
                              f"symbols")
        report.results = [SymbolResult(symbol=s, status="stale",
                                       error="fail-closed, day not corrected")
                          for s in pending]
        for symbol in pending:
            _mark(registry, symbol, period_key, "stale")
        logger.error("eod_correction fail-closed: %s — no file overwritten",
                     report.fail_closed)
        return report

    liquid = _liquid_symbols(registry)
    results: list[SymbolResult] = list(failures)
    for failure in failures:
        _mark(registry, failure.symbol, period_key, "stale")

    for symbol, df in fetched.items():
        results.append(_correct_symbol(symbol, df, day, period_key, open_ts, close_ts,
                                       expected=expected, liquid=symbol in liquid,
                                       provider=provider, registry=registry,
                                       now=now_ts))

    for symbol in hour_only:
        rows, error = sync_hours(symbol, day, provider=provider, registry=registry,
                                 now=now_ts)
        if error is None:
            report.hour_healed.append(symbol)
        else:
            _mark(registry, symbol, period_key, "partial",
                  meta={"hour_sync_error": error})
            results.append(SymbolResult(symbol=symbol, status="partial",
                                        hour_rows=rows, error=error))

    report.results = sorted(results, key=lambda r: r.symbol)
    logger.info("eod_correction %s: %s", period_key, report.summary())
    return report


def _correct_symbol(symbol: str, sip: pd.DataFrame, day: date, period_key: str,
                    open_ts, close_ts, *, expected: int, liquid: bool, provider,
                    registry, now) -> SymbolResult:
    stored_row = registry.get_row(symbol, TIMEFRAME, period_key)
    comparison = None
    if stored_row is not None and stored_row.provider != provider.provider_key:
        # the half-open window: a 20:00 ET close lands on UTC midnight in EDT,
        # which read_bars would read as "through that day"
        iex = store.read_bars(symbol, TIMEFRAME, open_ts,
                              close_ts - pd.Timedelta(nanoseconds=1))
        if not iex.empty:
            comparison = compare_feeds(iex, sip)

    rows = store.write_bars(symbol, TIMEFRAME, period_key, sip,
                            provider=provider.provider_key, replace=True,
                            registry=registry)
    completeness = round(rows / expected, 4) if expected else None
    rth_expected = rth_expected_minutes(day)
    rth_completeness = (round(rth_rows(sip, day) / rth_expected, 4)
                        if rth_expected else None)
    status = "partial" if (liquid and rth_completeness is not None
                           and rth_completeness < MIN_RTH_COMPLETENESS) else "ok"
    meta = {"completeness": completeness, "rth_completeness": rth_completeness,
            "expected_minutes": expected, "rth_expected_minutes": rth_expected}
    if comparison is not None:
        meta["iex_vs_sip"] = comparison

    hour_rows, hour_error = sync_hours(symbol, day, provider=provider,
                                       registry=registry, now=now)
    if hour_error is not None:
        # the 1hour gap has no watchdog of its own (it only reads 1min rows), so
        # it rides on the 1min row's status to stay in tomorrow's retry set
        status = "partial"
        meta["hour_sync_error"] = hour_error

    _mark(registry, symbol, period_key, status, meta=meta)
    return SymbolResult(symbol=symbol, status=status, rows=rows, hour_rows=hour_rows,
                        completeness=completeness, rth_completeness=rth_completeness,
                        comparison=comparison, error=hour_error)


def _mark(registry, symbol: str, period_key: str, status: str,
          meta: dict | None = None) -> None:
    try:
        registry.update_status(symbol, TIMEFRAME, period_key, status=status, meta=meta)
    except Exception:
        logger.warning("%s %s: registry status update failed", symbol, period_key,
                       exc_info=True)


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="CAT end-of-day SIP correction")
    ap.add_argument("--date", default=None,
                    help="ET trading day YYYY-MM-DD (default: last completed session)")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated subset (default: the stream's set + stored days)")
    args = ap.parse_args()

    logging.basicConfig(level="INFO",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    subset = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
              if args.symbols else None)
    report = run(args.date, symbols=subset)
    print(f"DONE: {report.summary()}")
    for r in report.results:
        print(f"  {r.symbol:<6} {r.status:<7} rows={r.rows:<4} 1hour={r.hour_rows:<3} "
              f"completeness={r.completeness} rth={r.rth_completeness} "
              f"iex_vs_sip={r.comparison} "
              f"{r.error or ''}")
    sys.exit(1 if report.fail_closed else 0)
