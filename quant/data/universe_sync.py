"""Nightly whole-market sync for the screener (``market.universe_sync``, bulk queue).

For the last completed session: refresh the securities universe, top up
corporate actions, sync daily bars (new listings via the batched backfill,
capped per night), rebuild the panel and mark the session complete or not,
refresh a rotating fifth of SEC share facts, and fetch sectors for the names
an active screener group would pool. Runs on the bulk worker so it never
competes with signal_cycle or the heartbeat for the main worker's slots.

Three beat slots (22:15 / 02:15 / 10:15 UTC) all target the same session; once
it is complete the later slots are no-ops. A non-blocking flock keeps the
beat and a manual run from overlapping.

  python -m quant.data.universe_sync          one nightly pass
  python -m quant.data.universe_sync --init   first load, stage by stage, resumable
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from zoneinfo import ZoneInfo

from quant import config
from quant.data import (backfill, calendar, corporate_actions, edgar, fetch, panel,
                        registry, securities)

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
DATA_DONE_ET = time(16, 20)
EDGAR_PER_NIGHT = 1500
SECTORS_PER_NIGHT = 300
FRESH_PENDING_RATIO = 0.05
BACKFILL_BATCH = 50
BACKFILL_PAUSE_S = 3.0
LOCK_PATH = config.DATA_ROOT / "_locks" / "universe_sync.lock"
_clock = monotonic


class Busy(RuntimeError):
    pass


@dataclass
class SyncReport:
    session: date | None = None
    skipped: str | None = None
    fail_closed: str | None = None
    complete: bool = False
    meta: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.skipped:
            return f"skipped: {self.skipped}"
        text = f"session={self.session} complete={self.complete}"
        return f"{text} fail_closed={self.fail_closed}" if self.fail_closed else text


def last_completed_session(now: datetime) -> date:
    """The latest trading day whose data is final: ET 16:20 of that day has passed."""
    now_et = now.astimezone(_ET)
    d = now_et.date()
    if calendar.is_trading_day(d) and now_et.time() >= DATA_DONE_ET:
        return d
    return calendar.previous_session(d)


@contextmanager
def _lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy("universe_sync already running") from None
        yield
    finally:
        handle.close()


def _free_gb() -> float:
    return shutil.disk_usage(config.BARS_ROOT if config.BARS_ROOT.exists()
                             else config.REPO_ROOT).free / 1e9


def _sync_actions(codes: list[str], today: date) -> dict:
    """Full history for never-synced codes, a recent window for the rest. The
    window reaches back to the last successful sync (+5 days of overlap), so an
    outage longer than CA_RECENT_DAYS cannot leave actions unfetched."""
    never = [c for c in codes if corporate_actions.load_synced_at(c) is None]
    never_set = set(never)
    known = [c for c in codes if c not in never_set]
    last = load_ca_last_success()
    days = config.CA_RECENT_DAYS if last is None else max(
        config.CA_RECENT_DAYS, (today - last).days + 5)
    stats = {"full": len(never), "recent": len(known), "recent_days": days}
    if never:
        corporate_actions.sync_universe(never, progress=lambda m: None)
    if known:
        corporate_actions.sync_recent(known, days=days)
    record_ca_success(today)
    return stats


_SYNC_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS fresh_empty (
    symbol        TEXT PRIMARY KEY,
    empty_count   INTEGER NOT NULL,   -- consecutive sessions the backfill came back empty
    last_session  TEXT NOT NULL       -- ISO session of the latest empty attempt
);
CREATE TABLE IF NOT EXISTS universe_sync_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def _state_conn():
    conn = corporate_actions._connect()
    conn.executescript(_SYNC_STATE_SCHEMA)
    return conn


def load_ca_last_success() -> date | None:
    conn = _state_conn()
    try:
        row = conn.execute("SELECT value FROM universe_sync_state "
                           "WHERE key = 'ca_last_success'").fetchone()
    finally:
        conn.close()
    return date.fromisoformat(row[0]) if row else None


def record_ca_success(day: date) -> None:
    conn = _state_conn()
    try:
        conn.execute("INSERT INTO universe_sync_state (key, value) "
                     "VALUES ('ca_last_success', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (day.isoformat(),))
        conn.commit()
    finally:
        conn.close()


def load_fresh_empty(codes) -> dict[str, tuple[int, date]]:
    conn = _state_conn()
    try:
        rows = conn.execute("SELECT symbol, empty_count, last_session FROM fresh_empty"
                            ).fetchall()
    finally:
        conn.close()
    wanted = set(codes)
    return {r[0]: (r[1], date.fromisoformat(r[2])) for r in rows if r[0] in wanted}


def record_fresh_results(empty: list[str], stored: list[str], session: date) -> None:
    """Count one empty attempt per session (the three beat slots of an
    incomplete night retry the same session and must not count thrice); any
    stored bars clear the record."""
    conn = _state_conn()
    try:
        conn.executemany(
            "INSERT INTO fresh_empty (symbol, empty_count, last_session) VALUES (?, 1, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET "
            "empty_count = empty_count + (excluded.last_session > last_session), "
            "last_session = max(last_session, excluded.last_session)",
            [(c, session.isoformat()) for c in empty])
        conn.executemany("DELETE FROM fresh_empty WHERE symbol = ?", [(c,) for c in stored])
        conn.commit()
    finally:
        conn.close()


def is_dormant(state: tuple[int, date] | None) -> bool:
    return state is not None and state[0] >= config.FRESH_EMPTY_DORMANT_AFTER


def _retry_due(state: tuple[int, date], session: date) -> bool:
    return (session - state[1]).days >= config.FRESH_DORMANT_RETRY_DAYS


def _sync_bars(codes: list[str], now: datetime, fresh_cap: int, session: date) -> dict:
    """Bars for tonight. New listings go through the batched backfill, capped
    per night; one that keeps coming back empty turns dormant after
    FRESH_EMPTY_DORMANT_AFTER sessions — it leaves the queue (bar a retry every
    FRESH_DORMANT_RETRY_DAYS) and the completeness ratio, so a few listings
    with no tape cannot hold the panel incomplete forever."""
    fresh = [c for c in codes if registry.max_last_ts(c, "daily") is None]
    fresh_set = set(fresh)
    current = [c for c in codes if c not in fresh_set]
    state = load_fresh_empty(fresh)
    active = [c for c in fresh if not is_dormant(state.get(c))]
    due = [c for c in fresh if is_dormant(state.get(c)) and _retry_due(state[c], session)]
    batch = (active + due)[:fresh_cap]
    stored, empty = 0, set()
    if batch:
        st = backfill.run_daily_backfill(batch, now=now, batch_size=BACKFILL_BATCH,
                                         pause_s=BACKFILL_PAUSE_S,
                                         progress=lambda m: logger.info("universe_sync: %s", m))
        stored = st.stored
        empty = set(st.empty)
        record_fresh_results(sorted(empty), [c for c in batch if c not in empty], session)
    state = load_fresh_empty(fresh)
    dormant = {c for c in fresh if is_dormant(state.get(c))}
    synced, failed = fetch.sync_daily_many(current, now=now) if current else (0, [])
    landed = set(batch) - empty
    pending = [c for c in fresh if c not in dormant and c not in landed]
    return {"fresh": len(fresh), "fresh_stored": stored,
            "fresh_pending": len(pending), "dormant": len(dormant),
            "current": len(current), "synced": synced, "failed": len(failed)}


def is_complete(bars: dict, n_eligible: int) -> bool:
    """Dormant listings count in neither side of the fresh-pending ratio."""
    return (bars["failed"] <= config.UNIVERSE_FAIL_RATIO * bars["current"]
            and bars["fresh_pending"]
            <= FRESH_PENDING_RATIO * (n_eligible - bars.get("dormant", 0)))


def _screener_groups() -> list[tuple[str, dict]]:
    rows = registry._run(
        "select name, screener_json from strategy_groups "
        "where status='active' and screener_json <> '{}'", (), fetch="all")
    return [(r[0], r[1]) for r in rows]


def _sync_pool_sectors(session: date) -> dict:
    from quant.data import screen, sectors
    from quant.engine import screener

    pooled: set[str] = set()
    for name, obj in _screener_groups():
        try:
            spec = screener.parse_spec(obj)
            if spec is not None:
                pooled |= set(screen.run_screen(spec, session).pool)
        except edgar.TIME_LIMIT_ERRORS:
            raise
        except Exception as e:
            logger.warning("universe_sync: screen for group %s failed: %s", name, e)
    known = sectors.load_sectors()
    missing = sorted(pooled - set(known))
    if missing:
        sectors.sync_sectors(missing[:SECTORS_PER_NIGHT], progress=lambda m: None)
    return {"pooled": len(pooled), "missing": len(missing),
            "fetched": min(len(missing), SECTORS_PER_NIGHT)}


def run(*, now: datetime | None = None,
        fresh_cap: int = config.UNIVERSE_FRESH_PER_NIGHT) -> SyncReport:
    now = now or datetime.now(timezone.utc)
    session = last_completed_session(now)
    report = SyncReport(session=session)
    try:
        with _lock():
            _run_locked(report, now, fresh_cap)
    except Busy:
        report.skipped = "busy"
    return report


def _run_locked(report: SyncReport, now: datetime, fresh_cap: int) -> None:
    """One nightly pass. Before each stage it checks a monotonic deadline set
    below celery's soft limit: the bar layer swallows SoftTimeLimitExceeded per
    chunk, so without it a long night would run on to the hard kill silently.
    A stage the deadline stops is not started; what already ran stands (the
    panel keeps the completeness it was given), and the beat says deadline."""
    session = report.session
    meta: dict = {"session": str(session)}
    report.meta = meta
    started = _clock()
    issues: list[str] = []

    def past_deadline(stage: str, *, incomplete: bool = False) -> bool:
        if _clock() - started < config.UNIVERSE_SYNC_DEADLINE_S:
            return False
        if incomplete:
            report.complete = meta["complete"] = False
        meta["deadline_stage"] = stage
        report.fail_closed = meta["fail_closed"] = "; ".join(issues + ["deadline"])
        registry.beat("universe_sync", meta)
        return True

    if _free_gb() < config.MIN_FREE_DISK_GB:
        report.fail_closed = meta["fail_closed"] = "disk"
        registry.beat("universe_sync", meta)
        return
    if panel.is_complete(session):
        report.skipped = "done"
        return

    try:
        meta["securities"] = securities.refresh(now.date())
    except edgar.TIME_LIMIT_ERRORS:
        raise
    except Exception as e:
        logger.exception("universe_sync: securities refresh failed")
        issues.append(f"securities refresh failed: {e}")
    codes = securities.eligible_codes()
    meta["eligible"] = len(codes)
    if not codes:
        issues.append("empty universe")
        report.fail_closed = meta["fail_closed"] = "; ".join(issues)
        registry.beat("universe_sync", meta)
        return

    if past_deadline("actions"):
        return
    try:
        meta["actions"] = _sync_actions(codes, now.date())
    except edgar.TIME_LIMIT_ERRORS:
        raise
    except Exception as e:
        logger.exception("universe_sync: corporate action sync failed")
        issues.append(f"corporate action sync failed: {e}")

    if past_deadline("bars"):
        return
    meta["bars"] = bars = _sync_bars(codes, now, fresh_cap, session)
    report.complete = meta["complete"] = is_complete(bars, len(codes))
    if not report.complete:
        issues.append(f"incomplete: {bars['failed']}/{bars['current']} bar sync failed, "
                      f"{bars['fresh_pending']} new listings pending")

    if past_deadline("panel", incomplete=True):
        return
    meta["panel"] = panel.update(session, codes, complete=report.complete)

    if past_deadline("edgar"):
        return
    try:
        meta["edgar"] = edgar.refresh_ciks(
            securities.ciks_to_check(session, EDGAR_PER_NIGHT), session)
    except edgar.TIME_LIMIT_ERRORS:
        raise
    except Exception as e:
        logger.exception("universe_sync: EDGAR refresh failed")
        meta["edgar"] = {"error": str(e)}

    if report.complete:
        if past_deadline("sectors"):
            return
        try:
            meta["sectors"] = _sync_pool_sectors(session)
        except edgar.TIME_LIMIT_ERRORS:
            raise
        except Exception as e:
            logger.exception("universe_sync: sector sync failed")
            meta["sectors"] = {"error": str(e)}

    if issues:
        report.fail_closed = meta["fail_closed"] = "; ".join(issues)
    registry.beat("universe_sync", meta)


def _stage(n: int, stats) -> None:
    print(f"STAGE {n} DONE {json.dumps(stats, default=str, sort_keys=True)}", flush=True)


def run_init(*, now: datetime | None = None) -> None:
    """First load, five resumable stages. Re-running the same command skips
    what is already done: synced actions, stored bars, checked CIKs."""
    now = now or datetime.now(timezone.utc)
    with _lock():
        _stage(1, securities.refresh(now.date()))
        codes = securities.eligible_codes()

        never = [c for c in codes if corporate_actions.load_synced_at(c) is None]
        if never:
            corporate_actions.sync_universe(never, progress=print)
        record_ca_success(now.date())
        _stage(2, {"eligible": len(codes), "synced_now": len(never)})

        st = backfill.run_daily_backfill(codes, now=now, batch_size=BACKFILL_BATCH,
                                         pause_s=BACKFILL_PAUSE_S, progress=print)
        _stage(3, {"total": st.total, "fresh": st.fresh, "stale": st.stale,
                   "current": st.current, "stored": st.stored, "empty": len(st.empty)})

        _stage(4, edgar.refresh_ciks(securities.unchecked_ciks(), now.date(),
                                     progress=print))

        start = now.date() - timedelta(days=365 * config.DAILY_HISTORY_YEARS + 7)
        _stage(5, panel.write_years(codes, range(start.year, now.year + 1)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Whole-market universe sync")
    ap.add_argument("--init", action="store_true", help="first load, stages 1-5")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.init:
        run_init()
        return 0
    report = run()
    print(report.summary())
    return 1 if report.fail_closed else 0


if __name__ == "__main__":
    sys.exit(main())
