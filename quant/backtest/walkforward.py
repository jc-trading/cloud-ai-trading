"""Walk-forward window generation (design §11: out-of-sample must hold up).

Calibrate params on the in-sample window, judge on the untouched out-of-sample
window, then roll forward. Prevents the R0-9 calibration from overfitting.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class WFWindow:
    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp


def walk_forward_windows(start, end, *, is_years: int = 3, oos_years: int = 1,
                         step_years: int = 1) -> list[WFWindow]:
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    windows: list[WFWindow] = []
    t = start
    while True:
        is_end = t + pd.DateOffset(years=is_years)
        oos_end = is_end + pd.DateOffset(years=oos_years)
        if oos_end > end:
            break
        windows.append(WFWindow(t, is_end, is_end, oos_end))
        t = t + pd.DateOffset(years=step_years)
    return windows
