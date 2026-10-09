"""One-time historical backfills: first a plan (requests, time, storage), then
the run — never the run without the plan being shown and approved.

A job never fetches more than asked; a span before an instrument's listing
simply returns nothing (it is not filled in). Funding and open interest are
fetched only where a venue offers them, and stored in their own tables.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from jarvis.market_data.adapters import (
    MIN_INTERVAL_S,
    Adapter,
    FundingPoint,
    Kind,
    OpenInterestPoint,
)
from jarvis.market_data.store import BarStore
from jarvis.trading.availability import Coverage, coverage
from jarvis.trading.instruments import Instrument

BYTES_PER_BAR = 110  # SQLite row incl. index, measured order of magnitude
FUNDING_INTERVAL_MS = 8 * 3_600_000
OI_INTERVAL_MS = 3_600_000
_PAGES = {"bybit": 1000, "okx": 100, "binance": 1000, "coinbase": 300}
_FUNDING_PAGES = {"bybit": 200, "okx": 100, "binance": 1000}
_OI_PAGES = {"bybit": 200, "binance": 500}


@dataclass(frozen=True)
class Job:
    venue: str
    instrument: Instrument
    kind: Kind
    interval_ms: int
    start_ms: int
    end_ms: int
    funding: bool = False
    open_interest: bool = False


@dataclass(frozen=True)
class Plan:
    jobs: tuple[Job, ...]
    requests: dict[str, int]
    bars: int
    est_seconds: float
    est_mb: float

    def summary(self) -> dict[str, Any]:
        return {
            "jobs": len(self.jobs),
            "requests": dict(self.requests),
            "bars": self.bars,
            "est_minutes": round(self.est_seconds / 60, 1),
            "est_mb": round(self.est_mb, 1),
        }


def plan(jobs: Sequence[Job]) -> Plan:
    requests: dict[str, int] = {}
    bars = 0
    for j in jobs:
        n = max(0, (j.end_ms - j.start_ms) // j.interval_ms)
        bars += n
        r = math.ceil(n / _PAGES[j.venue]) if n else 0
        span = j.end_ms - j.start_ms
        if j.funding and j.venue in _FUNDING_PAGES:
            r += math.ceil(span / FUNDING_INTERVAL_MS / _FUNDING_PAGES[j.venue])
        if j.open_interest and j.venue in _OI_PAGES:
            r += math.ceil(span / OI_INTERVAL_MS / _OI_PAGES[j.venue])
        requests[j.venue] = requests.get(j.venue, 0) + r
    # venues run one after another here; spacing is the bound, not latency
    seconds = sum(n * (MIN_INTERVAL_S[v] + 0.35) for v, n in requests.items())
    return Plan(tuple(jobs), requests, bars, seconds, bars * BYTES_PER_BAR / 1e6)


@dataclass
class Result:
    job: Job
    coverage: Coverage | None
    funding: list[FundingPoint] = field(default_factory=list)
    open_interest: list[OpenInterestPoint] = field(default_factory=list)
    error: str = ""


async def run(jobs: Sequence[Job], adapters: dict[str, Adapter], store: BarStore) -> list[Result]:
    out: list[Result] = []
    for j in jobs:
        adapter = adapters[j.venue]
        try:
            series = await adapter.bars(j.instrument, j.kind, j.interval_ms, j.start_ms, j.end_ms)
            cov = None
            if len(series):
                store.save(series)
                cov = coverage(series)
            res = Result(j, cov)
            if j.funding:
                res.funding = await adapter.funding(j.instrument, j.start_ms, j.end_ms)
                store.save_funding(
                    f"{j.venue}:perp:{adapter.symbol(j.instrument, Kind.PERP)}", res.funding
                )
            if j.open_interest:
                res.open_interest = await adapter.open_interest(j.instrument, j.start_ms, j.end_ms)
                store.save_open_interest(
                    f"{j.venue}:perp:{adapter.symbol(j.instrument, Kind.PERP)}", res.open_interest
                )
        except Exception as exc:  # noqa: BLE001 — one failed job is reported, the rest continue
            res = Result(j, None, error=f"{type(exc).__name__}: {exc}")
        out.append(res)
    return out


__all__ = ["Job", "Plan", "Result", "plan", "run"]
