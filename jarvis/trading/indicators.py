"""Causal technical indicators: the value at index ``i`` uses only data at
indices ``<= i`` (NaN until enough history exists). Deterministic, numpy only."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def _nan(n: int) -> FloatArray:
    return np.full(n, np.nan, dtype=np.float64)


def sma(x: FloatArray, n: int) -> FloatArray:
    out = _nan(len(x))
    if n <= 0 or len(x) < n:
        return out
    c = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def ema(x: FloatArray, n: int) -> FloatArray:
    out = _nan(len(x))
    if n <= 0 or len(x) < n:
        return out
    alpha = 2.0 / (n + 1)
    value = float(np.mean(x[:n]))
    out[n - 1] = value
    for i in range(n, len(x)):
        value += alpha * (float(x[i]) - value)
        out[i] = value
    return out


def true_range(high: FloatArray, low: FloatArray, close: FloatArray) -> FloatArray:
    prev = np.concatenate(([close[0]], close[:-1])) if len(close) else close
    return np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])


def atr(high: FloatArray, low: FloatArray, close: FloatArray, n: int = 14) -> FloatArray:
    """Wilder's average true range."""
    tr = true_range(high, low, close)
    out = _nan(len(tr))
    if n <= 0 or len(tr) < n:
        return out
    value = float(np.mean(tr[:n]))
    out[n - 1] = value
    for i in range(n, len(tr)):
        value = (value * (n - 1) + float(tr[i])) / n
        out[i] = value
    return out


def rsi(close: FloatArray, n: int = 14) -> FloatArray:
    """Wilder's relative strength index, 0..100."""
    out = _nan(len(close))
    if n <= 0 or len(close) <= n:
        return out
    delta = np.diff(close)
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    avg_g = float(np.mean(gain[:n]))
    avg_l = float(np.mean(loss[:n]))

    def _value(g: float, lo: float) -> float:
        if lo == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / lo)

    out[n] = _value(avg_g, avg_l)
    for i in range(n + 1, len(close)):
        avg_g = (avg_g * (n - 1) + float(gain[i - 1])) / n
        avg_l = (avg_l * (n - 1) + float(loss[i - 1])) / n
        out[i] = _value(avg_g, avg_l)
    return out


def prior_high(high: FloatArray, n: int) -> FloatArray:
    """Highest high of the ``n`` bars BEFORE ``i`` (a breakout reference)."""
    out = _nan(len(high))
    for i in range(n, len(high)):
        out[i] = float(np.max(high[i - n : i]))
    return out


def prior_low(low: FloatArray, n: int) -> FloatArray:
    """Lowest low of the ``n`` bars BEFORE ``i``."""
    out = _nan(len(low))
    for i in range(n, len(low)):
        out[i] = float(np.min(low[i - n : i]))
    return out


def realized_vol(close: FloatArray, n: int, periods_per_year: float) -> FloatArray:
    """Annualised standard deviation of log returns over the last ``n`` bars."""
    out = _nan(len(close))
    if len(close) <= n or n < 2:
        return out
    r = np.diff(np.log(close))
    for i in range(n, len(close)):
        out[i] = float(np.std(r[i - n : i], ddof=1)) * float(np.sqrt(periods_per_year))
    return out


__all__ = ["atr", "ema", "prior_high", "prior_low", "realized_vol", "rsi", "sma", "true_range"]
