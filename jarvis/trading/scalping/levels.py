"""Reference levels from COMPLETED periods: the daily open, the previous
day's and previous week's high and low (UTC). Computed from the bars
themselves — TradingView is optional, never required."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.data import DAY_MS, BarSeries

WEEK_MS = 7 * DAY_MS
_MONDAY_OFFSET = 4 * DAY_MS  # 1970-01-01 was a Thursday; Monday 1970-01-05 starts week 0

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class Levels:
    daily_open: FloatArray
    prev_day_high: FloatArray
    prev_day_low: FloatArray
    prev_week_high: FloatArray
    prev_week_low: FloatArray

    def at(self, i: int) -> dict[str, float]:
        return {k: float(getattr(self, k)[i]) for k in self.__dataclass_fields__}


def _period_ids(ts: NDArray[np.int64], length: int, offset: int = 0) -> NDArray[np.int64]:
    return (ts + offset) // length


def _previous_extremes(
    series: BarSeries, ids: NDArray[np.int64], length: int, offset: int
) -> tuple[FloatArray, FloatArray]:
    """For each bar: high/low of the previous period — only if that period is
    COMPLETED and was observed from its start (data that begins mid-period
    would give a partial, misleading extreme). NaN otherwise."""
    n = len(series)
    hi, lo = np.full(n, np.nan), np.full(n, np.nan)
    cur_id, cur_hi, cur_lo, cur_full = None, -np.inf, np.inf, False
    prev_hi, prev_lo = np.nan, np.nan
    for k in range(n):
        pid = int(ids[k])
        if pid != cur_id:
            if cur_id is not None:
                usable = pid == cur_id + 1 and cur_full
                prev_hi, prev_lo = (cur_hi, cur_lo) if usable else (np.nan, np.nan)
            cur_id, cur_hi, cur_lo = pid, -np.inf, np.inf
            cur_full = int(series.ts[k]) == pid * length - offset
        hi[k], lo[k] = prev_hi, prev_lo
        cur_hi = max(cur_hi, float(series.high[k]))
        cur_lo = min(cur_lo, float(series.low[k]))
    return hi, lo


def levels(series: BarSeries) -> Levels:
    days = _period_ids(series.ts, DAY_MS)
    weeks = _period_ids(series.ts, WEEK_MS, -_MONDAY_OFFSET)
    daily_open = np.full(len(series), np.nan)
    cur, value = None, np.nan
    for k in range(len(series)):
        if int(days[k]) != cur:
            cur, value = int(days[k]), float(series.open[k])
        daily_open[k] = value
    dh, dl = _previous_extremes(series, days, DAY_MS, 0)
    wh, wl = _previous_extremes(series, weeks, WEEK_MS, -_MONDAY_OFFSET)
    return Levels(daily_open, dh, dl, wh, wl)


__all__ = ["Levels", "WEEK_MS", "levels"]
