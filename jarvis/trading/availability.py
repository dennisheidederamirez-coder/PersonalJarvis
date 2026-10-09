"""What data exists, from when — and which instruments a test may use at a time.

Rules against look-ahead in the UNIVERSE (not just in prices):

- An instrument may only appear in a test from the moment it really traded
  on the source (its first bar there) plus a warm-up — never earlier, even
  if it is famous today.
- Instruments that stopped trading are kept in the record: a universe built
  only from today's survivors would flatter every historical test.
- Funding, open interest and order-flow features exist only for the spans a
  source actually covered; outside them a feature is missing, never filled.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jarvis.trading.data import BarSeries, assess


@dataclass(frozen=True)
class Coverage:
    source: str
    symbol: str
    interval_ms: int
    first_ms: int
    last_ms: int  # open time of the last bar
    bars: int
    gaps: int
    grade: str
    features: tuple[str, ...] = field(default_factory=tuple)  # e.g. ("taker_buy",)

    @property
    def end_ms(self) -> int:
        return self.last_ms + self.interval_ms

    def to_row(self) -> dict[str, object]:
        iso = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()  # noqa: E731
        return {
            "source": self.source,
            "symbol": self.symbol,
            "interval_min": self.interval_ms // 60_000,
            "from": iso(self.first_ms),
            "to": iso(self.end_ms),
            "bars": self.bars,
            "gaps": self.gaps,
            "grade": self.grade,
            "features": ",".join(self.features),
        }


def coverage(series: BarSeries) -> Coverage:
    if len(series) == 0:
        raise ValueError("empty series")
    q = assess(series)
    features = ("taker_buy",) if series.taker_buy is not None else ()
    return Coverage(
        series.source,
        series.instrument.symbol,
        series.interval_ms,
        int(series.ts[0]),
        int(series.ts[-1]),
        len(series),
        q.gaps,
        q.grade,
        features,
    )


@dataclass(frozen=True)
class Listing:
    symbol: str
    first_ms: int  # first bar seen on ANY source (a proxy for the listing)
    last_ms: int | None = None  # set when the instrument stopped trading (delisted)


def listings(covers: Iterable[Coverage]) -> dict[str, Listing]:
    out: dict[str, Listing] = {}
    for c in covers:
        prev = out.get(c.symbol)
        if prev is None or c.first_ms < prev.first_ms:
            out[c.symbol] = Listing(c.symbol, c.first_ms)
    return out


def eligible(
    universe: Sequence[Listing],
    at_ms: int,
    *,
    min_history_ms: int,
) -> list[str]:
    """Symbols a test may consider at *at_ms*: listed at least
    ``min_history_ms`` before, and not delisted yet."""
    return [
        item.symbol
        for item in universe
        if item.first_ms + min_history_ms <= at_ms
        and (item.last_ms is None or at_ms < item.last_ms)
    ]


def feature_span(series: BarSeries, values_ts: Sequence[int]) -> tuple[int, int] | None:
    """The bar-index range [start, end) of *series* covered by a feature whose
    observations carry the timestamps *values_ts* (e.g. funding, open
    interest). Outside it the feature does not exist for that test."""
    if not values_ts:
        return None
    lo, hi = min(values_ts), max(values_ts)
    import numpy as np

    start = int(np.searchsorted(series.ts, lo, side="left"))
    end = int(np.searchsorted(series.ts, hi, side="right"))
    return (start, end) if end > start else None


__all__ = ["Coverage", "Listing", "coverage", "eligible", "feature_span", "listings"]
