"""OHLCV bars with provenance, and a data-quality report.

Every series knows where it came from (``source``) and when it was read
(``retrieved_at``); every analysis built on it carries both. A series whose
quality grade is ``unusable`` (out-of-order or impossible bars) is refused by
the backtester instead of producing confident numbers from broken data.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.instruments import Instrument

FloatArray = NDArray[np.float64]

HOUR_MS: Final = 3_600_000
DAY_MS: Final = 24 * HOUR_MS


class DataQualityError(ValueError):
    """The data is too broken to analyse or backtest."""


@dataclass(frozen=True)
class BarSeries:
    """Aligned OHLCV arrays, oldest first; ``ts`` is the bar OPEN time in ms UTC."""

    instrument: Instrument
    interval_ms: int
    ts: NDArray[np.int64]
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    volume: FloatArray
    source: str
    retrieved_at: str  # ISO-8601 UTC
    #: base volume bought by aggressive (taker) buyers per bar, when the venue
    #: reports it (e.g. Binance klines); None otherwise. Feeds the CVD.
    taker_buy: FloatArray | None = None

    def __len__(self) -> int:
        return int(self.ts.shape[0])

    def slice(self, start: int, end: int | None = None) -> BarSeries:
        sl = slice(start, end)
        return BarSeries(
            self.instrument,
            self.interval_ms,
            self.ts[sl],
            self.open[sl],
            self.high[sl],
            self.low[sl],
            self.close[sl],
            self.volume[sl],
            self.source,
            self.retrieved_at,
            None if self.taker_buy is None else self.taker_buy[sl],
        )

    @property
    def periods_per_year(self) -> float:
        return 365.0 * DAY_MS / self.interval_ms


def make_series(
    instrument: Instrument,
    interval_ms: int,
    rows: list[tuple[int, float, float, float, float, float]],
    *,
    source: str,
    retrieved_at: str | None = None,
    taker_buy: list[float] | None = None,
) -> BarSeries:
    arr = np.asarray(rows, dtype=np.float64).reshape(-1, 6)
    if taker_buy is not None and len(taker_buy) != len(arr):
        raise ValueError("taker_buy must have one value per bar")
    return BarSeries(
        instrument,
        interval_ms,
        arr[:, 0].astype(np.int64),
        arr[:, 1].copy(),
        arr[:, 2].copy(),
        arr[:, 3].copy(),
        arr[:, 4].copy(),
        arr[:, 5].copy(),
        source,
        retrieved_at or datetime.now(UTC).isoformat(timespec="seconds"),
        None if taker_buy is None else np.asarray(taker_buy, dtype=np.float64),
    )


@dataclass(frozen=True)
class DataQuality:
    bars: int
    gaps: int  # missing bars between first and last
    duplicates: int
    out_of_order: int
    bad_bars: int  # high < max(open, close), low > min(open, close), or price <= 0
    zero_volume: int
    outliers: int  # |log return| > 12 robust sigmas
    coverage: float  # bars present / bars expected
    score: float  # 0..1
    grade: str  # good / fair / poor / unusable
    source: str
    retrieved_at: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def assess(series: BarSeries) -> DataQuality:
    """Count what is wrong with *series*; grade it."""
    n = len(series)
    notes: list[str] = []
    if n == 0:
        return DataQuality(
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0.0,
            0.0,
            "unusable",
            series.source,
            series.retrieved_at,
            ("no bars",),
        )
    steps = np.diff(series.ts)
    duplicates = int(np.sum(steps == 0))
    out_of_order = int(np.sum(steps < 0))
    span = int(series.ts[-1] - series.ts[0]) if out_of_order == 0 else 0
    expected = span // series.interval_ms + 1 if span > 0 else n
    gaps = max(0, expected - (n - duplicates))
    o, h, lo, c = series.open, series.high, series.low, series.close
    bad = (h < np.maximum(o, c)) | (lo > np.minimum(o, c)) | (np.minimum(lo, o) <= 0)
    bad |= ~np.isfinite(o) | ~np.isfinite(h) | ~np.isfinite(lo) | ~np.isfinite(c)
    bad_bars = int(np.sum(bad))
    zero_volume = int(np.sum(series.volume <= 0))
    outliers = 0
    if n > 20 and bad_bars == 0:
        r = np.diff(np.log(c))
        mad = float(np.median(np.abs(r - np.median(r)))) * 1.4826
        if mad > 0:
            outliers = int(np.sum(np.abs(r - np.median(r)) > 12 * mad))
    coverage = (n - duplicates) / expected if expected else 0.0
    score = coverage
    score -= 0.5 * min(1.0, zero_volume / n)
    score -= 0.05 * min(10, outliers)
    score = max(0.0, min(1.0, score))
    if out_of_order or bad_bars:
        grade = "unusable"
        notes.append("out-of-order or impossible bars")
    elif score >= 0.98:
        grade = "good"
    elif score >= 0.9:
        grade = "fair"
    else:
        grade = "poor"
    if gaps:
        notes.append(f"{gaps} missing bars")
    if outliers:
        notes.append(f"{outliers} extreme moves (check the source)")
    return DataQuality(
        n,
        gaps,
        duplicates,
        out_of_order,
        bad_bars,
        zero_volume,
        outliers,
        round(coverage, 4),
        round(score, 4),
        grade,
        series.source,
        series.retrieved_at,
        tuple(notes),
    )


def require_usable(series: BarSeries) -> DataQuality:
    quality = assess(series)
    if quality.grade == "unusable":
        raise DataQualityError("; ".join(quality.notes) or "unusable data")
    return quality


class MarketDataSource(Protocol):
    """Where bars come from. Implementations must be free of charge and keyless
    unless the owner approved otherwise; network sources live outside this
    package (see the guard test) and hand over a ``BarSeries``."""

    name: str

    def bars(self, instrument: Instrument, interval_ms: int) -> BarSeries: ...


class CsvBarSource:
    """Recorded bars from a CSV file: ``ts,open,high,low,close,volume`` (ts in ms)."""

    def __init__(self, path: str | Path, *, name: str | None = None) -> None:
        self.path = Path(path)
        self.name = name or f"csv:{self.path.name}"

    def bars(self, instrument: Instrument, interval_ms: int) -> BarSeries:
        rows: list[tuple[int, float, float, float, float, float]] = []
        with self.path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rows.append(
                    (
                        int(row["ts"]),
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["volume"]),
                    )
                )
        stamp = datetime.fromtimestamp(self.path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        return make_series(instrument, interval_ms, rows, source=self.name, retrieved_at=stamp)


__all__ = [
    "DAY_MS",
    "HOUR_MS",
    "BarSeries",
    "CsvBarSource",
    "DataQuality",
    "DataQualityError",
    "MarketDataSource",
    "assess",
    "make_series",
    "require_usable",
]
