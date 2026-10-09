"""The research agent's deterministic part: a sourced, time-stamped technical
snapshot of one instrument.

Every finding names its source, the time of the last bar it used and the
data quality grade. Findings that need data this phase has no source for
(funding rates, open interest, liquidations, news, macro calendar, ETF flows,
on-chain) are listed as ``missing`` instead of being guessed; they plug in
as further ``Finding`` producers once the owner has chosen free sources.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import numpy as np

from jarvis.trading.data import DAY_MS, BarSeries, assess
from jarvis.trading.indicators import atr, realized_vol, rsi, sma

#: Research dimensions that need an external source not yet approved.
PENDING_SOURCES: Final[tuple[str, ...]] = (
    "funding_rate",
    "open_interest",
    "liquidations",
    "crypto_news",
    "macro_calendar",
    "etf_flows",
    "on_chain",
)


@dataclass(frozen=True, slots=True)
class Finding:
    key: str
    value: Any
    unit: str
    source: str
    as_of: str  # time of the newest bar used, ISO-8601 UTC
    quality: str  # the data grade behind it
    note: str = ""


@dataclass(frozen=True)
class Snapshot:
    symbol: str
    as_of: str
    findings: tuple[Finding, ...]
    quality: dict[str, Any]
    missing: tuple[str, ...] = field(default_factory=lambda: PENDING_SOURCES)

    def get(self, key: str) -> Any:
        return next((f.value for f in self.findings if f.key == key), None)


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).isoformat(timespec="minutes")


def _r(value: float, digits: int = 4) -> float | None:
    return round(value, digits) if math.isfinite(value) else None


def technical_snapshot(series: BarSeries) -> Snapshot:
    quality = assess(series)
    n = len(series)
    if n == 0:
        return Snapshot(series.instrument.symbol, "", (), quality.to_dict())
    last_ts = int(series.ts[-1]) + series.interval_ms  # the newest bar's close
    as_of = _iso(last_ts)
    src, grade = series.source, quality.grade
    c, v = series.close, series.volume
    per_day = max(1, DAY_MS // series.interval_ms)

    def f(key: str, value: Any, unit: str, note: str = "") -> Finding:
        return Finding(key, value, unit, src, as_of, grade, note)

    out = [f("last_price", _r(float(c[-1]), 8), series.instrument.quote)]
    if n > per_day:
        out.append(f("change_24h", _r(float(c[-1] / c[-1 - per_day] - 1)), "fraction"))
        out.append(f("volume_24h", _r(float(np.sum(v[-per_day:])), 2), series.instrument.base))
        out.append(
            f(
                "quote_volume_24h",
                _r(float(np.sum(v[-per_day:] * c[-per_day:])), 0),
                series.instrument.quote,
                "liquidity proxy",
            )
        )
    vol = realized_vol(c, min(n - 1, 30 * per_day), series.periods_per_year)
    out.append(f("realized_vol_30d", _r(float(vol[-1])), "annualised"))
    a = atr(series.high, series.low, c, 14)
    out.append(f("atr_pct", _r(float(a[-1] / c[-1])), "fraction of price"))
    out.append(f("rsi_14", _r(float(rsi(c, 14)[-1]), 1), "0-100"))
    s50, s200 = float(sma(c, 50)[-1]), float(sma(c, 200)[-1])
    trend = "unknown"
    if math.isfinite(s50) and math.isfinite(s200):
        trend = "up" if c[-1] > s50 > s200 else "down" if c[-1] < s50 < s200 else "range"
    out.append(f("trend", trend, "label", "close vs. SMA 50/200"))
    look = min(n, 20 * per_day)
    hi, lo = float(np.max(series.high[-look:])), float(np.min(series.low[-look:]))
    out.append(
        f(
            "range_20d_position",
            _r((float(c[-1]) - lo) / (hi - lo)) if hi > lo else None,
            "0=low 1=high",
            "market structure",
        )
    )
    return Snapshot(series.instrument.symbol, as_of, tuple(out), quality.to_dict())


__all__ = ["PENDING_SOURCES", "Finding", "Snapshot", "technical_snapshot"]
