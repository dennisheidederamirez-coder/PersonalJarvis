"""Synthetic, seeded OHLCV markets for trading tests — no network, no real data.

- ``random_walk``: driftless noise. No strategy has a real edge here; the
  validation must say "no trade".
- ``trending``: long regimes of persistent drift up or down. Trend followers
  have a genuine edge here.
- ``mean_reverting``: an Ornstein-Uhlenbeck price around a level.
"""

from __future__ import annotations

import numpy as np

from jarvis.trading.data import HOUR_MS, BarSeries, make_series
from jarvis.trading.instruments import BTC_USD, Instrument

T0 = 1_767_225_600_000  # 2026-01-01T00:00Z


def _bars(
    closes: np.ndarray, rng: np.random.Generator, instrument: Instrument, source: str
) -> BarSeries:
    rows = []
    prev = float(closes[0])
    for k, c in enumerate(closes):
        o = prev
        wick = abs(rng.normal(0, 0.002))
        h = max(o, float(c)) * (1 + wick)
        lo = min(o, float(c)) * (1 - abs(rng.normal(0, 0.002)))
        rows.append((T0 + k * HOUR_MS, o, h, lo, float(c), float(rng.uniform(50, 150))))
        prev = float(c)
    return make_series(
        instrument, HOUR_MS, rows, source=source, retrieved_at="2026-10-09T00:00:00+00:00"
    )


def random_walk(
    n: int = 4000, *, seed: int = 1, vol: float = 0.006, instrument: Instrument = BTC_USD
) -> BarSeries:
    rng = np.random.default_rng(seed)
    closes = 30_000 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    return _bars(closes, rng, instrument, f"fake:random_walk:{seed}")


def trending(
    n: int = 6000,
    *,
    seed: int = 2,
    drift: float = 0.004,
    vol: float = 0.005,
    regime: tuple[int, int] = (150, 300),
    instrument: Instrument = BTC_USD,
) -> BarSeries:
    rng = np.random.default_rng(seed)
    r = np.empty(n)
    k, sign = 0, 1.0
    while k < n:
        length = int(rng.integers(*regime))
        r[k : k + length] = sign * drift + rng.normal(0, vol, min(length, n - k))
        k += length
        sign = -sign
    closes = 30_000 * np.exp(np.cumsum(r))
    return _bars(closes, rng, instrument, f"fake:trending:{seed}")


def mean_reverting(
    n: int = 4000,
    *,
    seed: int = 3,
    theta: float = 0.08,
    vol: float = 0.008,
    instrument: Instrument = BTC_USD,
) -> BarSeries:
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for k in range(1, n):
        x[k] = x[k - 1] * (1 - theta) + rng.normal(0, vol)
    closes = 30_000 * np.exp(x)
    return _bars(closes, rng, instrument, f"fake:mean_reverting:{seed}")
