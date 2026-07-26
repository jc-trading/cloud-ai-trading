"""Signal representation — three independent fields, not one score (design §6.2).

Direction / Confidence / ExpectedMove are separate because "likely up a little"
and "unsure but maybe up a lot" need different position sizes and exits — a single
score can't tell them apart (LEAN Insight's three-field split).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


@dataclass(frozen=True)
class Signal:
    symbol: str
    direction: Direction
    confidence: float          # 0-100 (from MACD momentum + RSI filter)
    expected_move: float       # ATR-derived, price units (NOT an independent forecast)
    atr: float                 # latest ATR — stop distance is derived from this
    last_close: float
    generated_at: date
    expires_at: date | None = None
    source_model: str = "cat.v1.composite"
    reason: str = ""
    # per-component breakdown for the audit log / dashboard (design §5.1)
    components: dict = field(default_factory=dict)

    @property
    def is_long_entry(self) -> bool:
        """Long-only [A1]: only UP direction is tradable for entry."""
        return self.direction is Direction.UP
