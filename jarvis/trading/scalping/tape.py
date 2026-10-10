"""Individual trades and bars built from them.

A trade carries the aggressor side: ``buyer_aggressor=True`` means a market
buy hit the ask (the venue's "is buyer maker = false"). Bars built from a
tape therefore know their aggressive buy volume, which feeds delta and CVD.
Only venue-reported trades go in; nothing is interpolated.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.data import BarSeries, make_series
from jarvis.trading.instruments import Instrument


@dataclass(frozen=True)
class TradeTape:
    source: str  # "<venue>:<spot|perp>:<symbol>"
    ts: NDArray[np.int64]  # ms, non-decreasing
    price: NDArray[np.float64]
    qty: NDArray[np.float64]  # base units
    buyer_aggressor: NDArray[np.bool_]

    def __post_init__(self) -> None:
        n = len(self.ts)
        if not (len(self.price) == len(self.qty) == len(self.buyer_aggressor) == n):
            raise ValueError("tape columns differ in length")
        if n and (np.any(np.diff(self.ts) < 0)):
            raise ValueError("trades out of time order")
        if n and (np.any(self.price <= 0) or np.any(self.qty <= 0)):
            raise ValueError("non-positive price or quantity")

    def __len__(self) -> int:
        return int(len(self.ts))

    @classmethod
    def from_rows(cls, source: str, rows: list[tuple[int, float, float, bool]]) -> TradeTape:
        arr = sorted(rows, key=lambda r: r[0])
        return cls(
            source,
            np.asarray([r[0] for r in arr], dtype=np.int64),
            np.asarray([r[1] for r in arr], dtype=np.float64),
            np.asarray([r[2] for r in arr], dtype=np.float64),
            np.asarray([r[3] for r in arr], dtype=np.bool_),
        )

    def window(self, start_ms: int, end_ms: int) -> TradeTape:
        lo = int(np.searchsorted(self.ts, start_ms, side="left"))
        hi = int(np.searchsorted(self.ts, end_ms, side="left"))
        return TradeTape(
            self.source,
            self.ts[lo:hi],
            self.price[lo:hi],
            self.qty[lo:hi],
            self.buyer_aggressor[lo:hi],
        )


def to_bars(
    tape: TradeTape, instrument: Instrument, interval_ms: int, *, until_ms: int | None = None
) -> BarSeries:
    """OHLCV bars with aggressive-buy volume. A bar exists only if trades
    happened in it (an empty minute stays a gap, it is not invented), and
    only bars that ended by ``until_ms`` are returned."""
    if len(tape) == 0:
        return make_series(instrument, interval_ms, [], source=tape.source, taker_buy=[])
    buckets = tape.ts // interval_ms
    rows: list[tuple[int, float, float, float, float, float]] = []
    buys: list[float] = []
    k, n = 0, len(tape)
    while k < n:
        b = int(buckets[k])
        j = k
        while j < n and int(buckets[j]) == b:
            j += 1
        start = b * interval_ms
        if until_ms is None or start + interval_ms <= until_ms:
            p, q = tape.price[k:j], tape.qty[k:j]
            rows.append(
                (start, float(p[0]), float(p.max()), float(p.min()), float(p[-1]), float(q.sum()))
            )
            buys.append(float(q[tape.buyer_aggressor[k:j]].sum()))
        k = j
    return make_series(
        instrument, interval_ms, rows, source=f"{tape.source}@trades", taker_buy=buys
    )


__all__ = ["TradeTape", "to_bars"]
