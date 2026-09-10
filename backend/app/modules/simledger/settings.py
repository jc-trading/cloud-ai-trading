"""Master Settings read path (design §8.6, Phase C #12) — TIGHTEN-ONLY, read-only.

``master_settings`` exists to let a risk knob be tightened without a code change.
This module is the ONLY reader of that table, and it is deliberately half a
feature: there is no write API, no cache, and no way for a DB row to LOOSEN
anything. Each knob declares the constant it defaults to and which direction is
tighter; a stored value that is looser than the constant, non-finite, or keyed to
a name this registry does not know is REJECTED — the constant applies, an ERROR
is logged, and the caller alerts once per cycle.

The layering rule (拍板 2026-09-10): the platform's settings may only tighten;
the instance (today the sim account, later a strategy instance) chooses. So a
tightening knob composes as ``min(instance value, platform value)`` — see
``max_concurrent_slots``, applied over the equity ladder in ``run_entries``.

Seeded at the constants by migration 018, so t0 behaviour is unchanged.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quant import config as qconfig

from app.modules.simledger.cycles import RECOMMENDED_FUNNEL
from app.modules.simledger.models import MasterSetting

logger = logging.getLogger(__name__)

# tighter direction: "down" = a smaller value is stricter, "up" = a larger one is
TIGHTEN_DOWN = "down"
TIGHTEN_UP = "up"


@dataclass(frozen=True)
class Knob:
    key: str
    default: float
    tighten: str


# The six knobs the platform may tighten, each pinned to the constant it
# currently runs on. min_confidence reads the DEPLOYED funnel value (65) rather
# than config.MIN_CONFIDENCE (0) — the live override is the thing being tightened.
KNOBS: tuple[Knob, ...] = (
    Knob("per_trade_risk_pct", qconfig.PER_TRADE_RISK_PCT, TIGHTEN_DOWN),
    Knob("daily_loss_pause_pct", qconfig.DAILY_LOSS_PAUSE_PCT, TIGHTEN_DOWN),
    Knob("portfolio_drawdown_halt_pct", qconfig.PORTFOLIO_DRAWDOWN_HALT_PCT, TIGHTEN_DOWN),
    Knob("min_confidence", RECOMMENDED_FUNNEL.min_confidence, TIGHTEN_UP),
    Knob("intraday_entry_chase_cap", qconfig.INTRADAY_ENTRY_CHASE_CAP, TIGHTEN_DOWN),
    Knob("max_concurrent_slots", float(qconfig.POSITION_LADDER[-1][1]), TIGHTEN_DOWN),
)

_BY_KEY = {k.key: k for k in KNOBS}


@dataclass(frozen=True)
class EffectiveSettings:
    """What this cycle actually runs on. Injected into the cycle functions by the
    task layer — ``cycles.py`` never reads the DB."""

    per_trade_risk_pct: float
    daily_loss_pause_pct: float
    portfolio_drawdown_halt_pct: float
    min_confidence: float
    intraday_entry_chase_cap: float
    max_concurrent_slots: int
    rejected: tuple[str, ...] = ()


def _accepts(knob: Knob, value: float) -> bool:
    return value <= knob.default if knob.tighten == TIGHTEN_DOWN \
        else value >= knob.default


def _resolve(rows: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    values = {k.key: k.default for k in KNOBS}
    rejected: list[str] = []
    for key, raw in rows.items():
        knob = _BY_KEY.get(key)
        if knob is None:
            rejected.append(f"unknown key {key!r}")
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            rejected.append(f"{key}={raw!r} is not a number")
            continue
        if not math.isfinite(value):
            rejected.append(f"{key}={value} is not finite")
            continue
        if not _accepts(knob, value):
            rejected.append(f"{key}={value:g} is LOOSER than {knob.default:g} "
                            f"(may only be tightened {knob.tighten})")
            continue
        values[key] = value
    for message in rejected:
        logger.error("master_settings rejected: %s — running on the constant",
                     message)
    return values, rejected


async def effective_settings(db: AsyncSession) -> EffectiveSettings:
    """Read master_settings and compose it with the constants. Read once per
    cycle, never cached: a tightening must take effect on the next cycle without
    a restart, and the table has six rows."""
    rows = {r.key: r.value for r in (await db.execute(select(MasterSetting))).scalars()}
    values, rejected = _resolve(rows)
    return EffectiveSettings(
        per_trade_risk_pct=values["per_trade_risk_pct"],
        daily_loss_pause_pct=values["daily_loss_pause_pct"],
        portfolio_drawdown_halt_pct=values["portfolio_drawdown_halt_pct"],
        min_confidence=values["min_confidence"],
        intraday_entry_chase_cap=values["intraday_entry_chase_cap"],
        max_concurrent_slots=int(values["max_concurrent_slots"]),
        rejected=tuple(rejected),
    )


async def validate_settings(db: AsyncSession) -> list[str]:
    """Startup check: the rejection list the process should refuse to boot on
    (backend) or alert about (worker). Empty means every stored row is a legal
    tightening."""
    return list((await effective_settings(db)).rejected)
