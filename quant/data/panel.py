"""Daily cross-section panel for the screener: one Parquet file per year.

``cat-data/panel/us/daily/{YYYY}.parquet`` holds long rows
``date, code, close, volume, ret`` — close and volume RAW (price-level fields),
``ret`` the split/dividend-adjusted simple return (ratio fields). It is a
derived cache of the bar store: every write rebuilds whole year files from
``store.read_bars`` and lands them with a temp file + atomic rename, so a
reader never sees a half-written year and a failed rebuild leaves the old file.

``_sessions.json`` records, per session, whether that night's sync was
complete enough to screen on; ``screen.run_screen`` refuses an incomplete one.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from quant import config
from quant.data import bars, calendar, corporate_actions, store

ROW_GROUP_CODES = 500
_COLUMNS = ["date", "code", "close", "volume", "ret"]
_SESSIONS_FILE = "_sessions.json"
_ET = "America/New_York"


def year_path(year: int) -> Path:
    return config.PANEL_ROOT / f"{year:04d}.parquet"


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"date": pd.Series(dtype="object"),
                         "code": pd.Series(dtype="object"),
                         "close": pd.Series(dtype="float64"),
                         "volume": pd.Series(dtype="float64"),
                         "ret": pd.Series(dtype="float64")})


def symbol_rows(code: str, start: date, end: date) -> pd.DataFrame:
    """One symbol's panel rows for [start, end]. Reads 20 extra days before
    start so the first row's return and any dividend factor near start have
    their prior close. A symbol whose adjustment is suspect (``is_unadjusted``)
    is cut at the session its actions are authoritative through, or skipped
    when it was never action-synced."""
    raw = store.read_bars(code, "daily", start - timedelta(days=20), end)
    if raw.empty:
        return _empty()
    actions = corporate_actions.load_actions(code)
    synced_at = corporate_actions.load_synced_at(code)
    day = raw["ts"].dt.tz_convert(_ET).dt.date
    if bars.is_unadjusted(code, raw, actions=actions, synced_at=synced_at):
        if synced_at is None:
            return _empty()
        keep = (day <= synced_at).to_numpy()
        raw, day = raw[keep].reset_index(drop=True), day[keep].reset_index(drop=True)
        if raw.empty:
            return _empty()
    adj = corporate_actions.adjust(raw, actions, mode="split_div")
    out = pd.DataFrame({"date": day, "code": code.upper(),
                        "close": raw["close"].astype("float64"),
                        "volume": raw["volume"].astype("float64"),
                        "ret": adj["close"].astype("float64").pct_change()})
    return out[(out["date"] >= start) & (out["date"] <= end)].reset_index(drop=True)


def _schema():
    import pyarrow as pa

    return pa.schema([("date", pa.date32()), ("code", pa.string()),
                      ("close", pa.float64()), ("volume", pa.float64()),
                      ("ret", pa.float64())])


def _tmp(path: Path) -> Path:
    return path.parent / f"{path.name}.tmp-{os.getpid()}"


def load_sessions() -> dict[str, bool]:
    path = config.PANEL_ROOT / _SESSIONS_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_sessions(flags: dict[str, bool]) -> None:
    path = config.PANEL_ROOT / _SESSIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    try:
        tmp.write_text(json.dumps(dict(sorted(flags.items())), indent=1))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_years(codes, years, *, complete_sessions: dict | None = None) -> dict:
    """Rebuild the given year files for ``codes`` (one row group per 500 codes)
    and then merge ``complete_sessions`` ({session: bool}) into _sessions.json.
    Each code's bars are read once for the whole span; every year file is
    written to a temp path and only renamed into place after all succeeded."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    years = sorted(set(years))
    if not years:
        return {"years": [], "rows": 0}
    codes = sorted({c.upper() for c in codes})
    lo, hi = date(years[0], 1, 1), date(years[-1], 12, 31)
    config.PANEL_ROOT.mkdir(parents=True, exist_ok=True)
    schema = _schema()
    writers = {y: pq.ParquetWriter(_tmp(year_path(y)), schema,
                                   compression=config.PARQUET_COMPRESSION)
               for y in years}
    rows = {y: 0 for y in years}
    try:
        for i in range(0, len(codes), ROW_GROUP_CODES):
            frames = [symbol_rows(c, lo, hi) for c in codes[i:i + ROW_GROUP_CODES]]
            frames = [f for f in frames if not f.empty]
            if not frames:
                continue
            chunk = pd.concat(frames, ignore_index=True)
            chunk_year = pd.Series([d.year for d in chunk["date"]], index=chunk.index)
            for y in years:
                part = chunk[chunk_year == y]
                if part.empty:
                    continue
                writers[y].write_table(pa.Table.from_pandas(
                    part[_COLUMNS], schema=schema, preserve_index=False))
                rows[y] += len(part)
        for w in writers.values():
            w.close()
        for y in years:
            os.replace(_tmp(year_path(y)), year_path(y))
    except BaseException:
        for y, w in writers.items():
            try:
                w.close()
            except Exception:
                pass
            _tmp(year_path(y)).unlink(missing_ok=True)
        raise
    if complete_sessions:
        flags = load_sessions()
        flags.update({str(k): bool(v) for k, v in complete_sessions.items()})
        _save_sessions(flags)
    return {"years": years, "rows": sum(rows.values()), "codes": len(codes)}


def rebuild_sessions(session: date) -> list[date]:
    start = calendar.advance(session, -(config.PANEL_REBUILD_SESSIONS - 1))
    return calendar.sessions_in_range(start, session)


def update(session: date, codes, *, complete: bool) -> dict:
    """Nightly: rebuild every year touched by the last PANEL_REBUILD_SESSIONS
    sessions (late corporate actions re-adjust recent returns) and record
    whether ``session`` is complete."""
    years = sorted({d.year for d in rebuild_sessions(session)})
    return write_years(codes, years, complete_sessions={session: complete})


def read(codes, sessions) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Wide (session x code) close, volume and ret for the given sessions;
    a session or code absent from the panel is all-NaN."""
    import pyarrow.parquet as pq

    sessions = sorted(sessions)
    codes = sorted({c.upper() for c in codes})
    frames = []
    for y in sorted({d.year for d in sessions}):
        path = year_path(y)
        if not path.exists():
            continue
        filters = [("date", ">=", sessions[0]), ("date", "<=", sessions[-1])]
        if codes:
            filters.append(("code", "in", codes))
        frames.append(pq.read_table(path, filters=filters).to_pandas())
    long = pd.concat(frames, ignore_index=True) if frames else _empty()
    long = long[long["date"].isin(set(sessions))]
    out = []
    for col in ("close", "volume", "ret"):
        wide = long.pivot(index="date", columns="code", values=col) if not long.empty \
            else pd.DataFrame()
        out.append(wide.reindex(index=sessions, columns=codes).astype("float64"))
    return out[0], out[1], out[2]


def is_complete(session: date) -> bool:
    return bool(load_sessions().get(str(session)))


def latest_complete() -> date | None:
    done = [k for k, v in load_sessions().items() if v]
    return date.fromisoformat(max(done)) if done else None
