"""Multi-timeframe support without look-ahead.

Supported intervals: 1m, 3m, 5m, 15m, 1h, 4h, 1d. Strategies on 1m-5m bars
belong to the scalping research branch (docs/trading-scalping.md); anything
shorter (seconds) needs trade or order-book data, not bars. A higher-timeframe value is used
only once its bar has CLOSED: at a lower bar's close, a strategy sees the most
recent higher bar whose close time is at or before that moment — never the
higher bar that is still forming.

Higher timeframes are built by aggregating the SAME lower series (one venue,
one market kind). A bucket with missing lower bars is dropped instead of
being filled in, so a gap never turns into an invented candle.

``HigherTimeframeFilter`` wraps any strategy with an optional trend
confirmation from a higher timeframe. It is a separate strategy with its own
name: it has to prove its benefit in the walk-forward comparison against the
unfiltered strategy, like everything else.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.data import DAY_MS, HOUR_MS, BarSeries, make_series
from jarvis.trading.indicators import sma
from jarvis.trading.strategies import Side, Strategy, Target

M1: Final = 60_000
M3: Final = 3 * 60_000
M5: Final = 5 * 60_000
M15: Final = 15 * 60_000
H1: Final = HOUR_MS
H4: Final = 4 * HOUR_MS
D1: Final = DAY_MS
SUPPORTED: Final = (M1, M3, M5, M15, H1, H4, D1)
LABEL: Final = {M1: "1m", M3: "3m", M5: "5m", M15: "15m", H1: "1h", H4: "4h", D1: "1d"}


def resample(series: BarSeries, interval_ms: int) -> BarSeries:
    """Aggregate *series* into complete bars of *interval_ms* (UTC-aligned)."""
    if interval_ms % series.interval_ms or interval_ms < series.interval_ms:
        raise ValueError("target interval must be a multiple of the source interval")
    per = interval_ms // series.interval_ms
    buckets = series.ts // interval_ms
    rows: list[tuple[int, float, float, float, float, float]] = []
    buys: list[float] = []
    k = 0
    n = len(series)
    while k < n:
        b = buckets[k]
        j = k
        while j < n and buckets[j] == b:
            j += 1
        complete = (j - k == per) and int(series.ts[k]) == int(b) * interval_ms
        if complete:
            rows.append(
                (
                    int(b) * interval_ms,
                    float(series.open[k]),
                    float(np.max(series.high[k:j])),
                    float(np.min(series.low[k:j])),
                    float(series.close[j - 1]),
                    float(np.sum(series.volume[k:j])),
                )
            )
            if series.taker_buy is not None:
                buys.append(float(np.sum(series.taker_buy[k:j])))
        k = j
    return make_series(
        series.instrument,
        interval_ms,
        rows,
        source=f"{series.source}@{LABEL.get(interval_ms, interval_ms)}",
        retrieved_at=series.retrieved_at,
        taker_buy=buys if series.taker_buy is not None else None,
    )


def align(lower: BarSeries, higher: BarSeries) -> NDArray[np.int64]:
    """For each lower bar, the index of the latest higher bar CLOSED by the
    lower bar's close (-1 when none has closed yet)."""
    lower_close = lower.ts + lower.interval_ms
    higher_close = higher.ts + higher.interval_ms
    return np.searchsorted(higher_close, lower_close, side="right").astype(np.int64) - 1


class HigherTimeframeFilter:
    """Take the inner strategy's entries only in the direction of the higher
    timeframe trend (fast SMA vs. slow SMA of CLOSED higher bars). Exits and
    flat targets pass through unchanged."""

    def __init__(
        self,
        inner: Strategy,
        higher_ms: int,
        *,
        fast: int = 20,
        slow: int = 50,
        name: str | None = None,
    ) -> None:
        if higher_ms not in SUPPORTED:
            raise ValueError("unsupported higher timeframe")
        self.inner, self.higher_ms, self.fast, self.slow = inner, higher_ms, fast, slow
        self.name = name or f"{inner.name}+trend{LABEL[higher_ms]}"
        self.params: Mapping[str, Any] = {
            **dict(inner.params),
            "htf": LABEL[higher_ms],
            "htf_fast": fast,
            "htf_slow": slow,
        }
        self.timeframes = (None, higher_ms)  # execution timeframe comes from the series

    def prepare(self, series: BarSeries) -> None:
        if self.higher_ms <= series.interval_ms:
            raise ValueError("the filter timeframe must be higher than the series")
        self.inner.prepare(series)
        higher = resample(series, self.higher_ms)
        f, s = sma(higher.close, self.fast), sma(higher.close, self.slow)
        idx = align(series, higher)
        trend = np.zeros(len(series), dtype=np.int8)
        ok = idx >= 0
        fi, si = f[np.maximum(idx, 0)], s[np.maximum(idx, 0)]
        valid = ok & np.isfinite(fi) & np.isfinite(si)
        trend[valid & (fi > si)] = 1
        trend[valid & (fi < si)] = -1
        self._trend = trend

    def explain(self, i: int, current: Side | None) -> str:
        inner = getattr(self.inner, "explain", None)
        trend = {1: "up", -1: "down"}.get(int(self._trend[i]), "undecided")
        base = inner(i, current) if inner else "no signal"
        return f"{base}; higher-timeframe trend {trend}"

    def decide(self, i: int, current: Side | None) -> Target | None:
        target = self.inner.decide(i, current)
        if target is None or target.side is None:
            return target
        if int(self._trend[i]) != target.side.sign:
            # against (or without) the higher trend: no new entry; an existing
            # position is left to its stop, target and the inner exits
            return Target(None, reason="against the higher-timeframe trend") if current else None
        return target


__all__ = [
    "D1",
    "H1",
    "H4",
    "LABEL",
    "M1",
    "M3",
    "M5",
    "M15",
    "SUPPORTED",
    "HigherTimeframeFilter",
    "align",
    "resample",
]
