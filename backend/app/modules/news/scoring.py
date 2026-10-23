"""Source scoring (P7a): a Beta posterior of each source's hit rate shrunk toward
the global hit rate p0, and its lift over p0. p0 drifts with the data, so every
source moves together and the auto-disable threshold is on lift, never on the
raw score. Pure functions."""

from __future__ import annotations

from app.config import settings

_P0_FLOOR = 0.01


def prior_mean(hits: int, n: int, s=settings) -> float:
    if n >= s.NEWS_PRIOR_MIN_GLOBAL_N:
        return max(hits / n, _P0_FLOOR)
    return s.NEWS_PRIOR_MEAN_DEFAULT


def posterior(hits: int, n: int, p0: float, k: float) -> float:
    return (k * p0 + hits) / (k + n)


def lift(post: float, p0: float) -> float:
    return post / p0


def brier(pairs: list[tuple[int, bool]]) -> float | None:
    if not pairs:
        return None
    return sum((p / 100 - int(hit)) ** 2 for p, hit in pairs) / len(pairs)


def should_auto_disable(*, enabled: bool, kind: str, n: int, auto_disable_after_n: int,
                        lifts_desc: list[float], s=settings) -> bool:
    k = s.NEWS_DISABLE_CONSECUTIVE_DAYS
    return (enabled and kind != "platform"
            and n >= max(s.NEWS_SOURCE_MIN_N, auto_disable_after_n)
            and len(lifts_desc) >= k
            and all(x < s.NEWS_DISABLE_BELOW_LIFT for x in lifts_desc[:k]))
