"""Does an external signal carry information? An event study against
independent bars, compared with random timing.

For each signal: find the bar it belongs to in an INDEPENDENT price series
(not the provider's), assume entry at the NEXT bar's open (the earliest a
signal on a closed bar can be acted on) and measure the direction-adjusted
return after each horizon, minus round-trip costs. The same is done for many
random entry times (same count, same directions): the p-value is the share
of random draws whose mean is at least as good. A signal family is only
"informative" with enough events and a small p-value — otherwise it is noise,
however good the chart looks.

Also reported: how many signals could not be matched to a bar (data gaps or
symbol/interval mismatch) and how many disagreed with the independent price
by more than a tolerance — a cheap check that the provider and our data
describe the same market.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from jarvis.trading.data import BarSeries
from jarvis.trading.signals import ExternalSignal


@dataclass(frozen=True)
class HorizonResult:
    horizon_bars: int
    events: int
    mean_return: float  # direction-adjusted, after costs
    hit_rate: float
    p_value: float


@dataclass(frozen=True)
class SignalStudy:
    family: str
    signals: int
    matched: int
    unmatched: int
    price_mismatches: int
    horizons: tuple[HorizonResult, ...]
    informative: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _forward(
    series: BarSeries, idx: np.ndarray, signs: np.ndarray, h: int, cost: float
) -> np.ndarray:
    entry = series.open[idx + 1]
    exit_ = series.close[np.minimum(idx + h, len(series) - 1)]
    return signs * (exit_ / entry - 1) - cost


def study(
    family: str,
    signals: Sequence[ExternalSignal],
    series: BarSeries,
    *,
    horizons: Sequence[int] = (1, 4, 24),
    round_trip_cost: float = 0.002,
    price_tolerance: float = 0.005,
    min_events: int = 30,
    alpha: float = 0.05,
    draws: int = 2000,
    seed: int = 11,
) -> SignalStudy:
    pos = {int(t): k for k, t in enumerate(series.ts)}
    max_h = max(horizons)
    idx_list: list[int] = []
    signs: list[int] = []
    unmatched = mismatches = 0
    for s in signals:
        k = pos.get(s.bar_time_ms)
        if k is None or k + max_h >= len(series) or s.direction == "neutral":
            unmatched += 1
            continue
        if s.price is not None:
            ref = float(series.close[k])
            if abs(s.price / ref - 1) > price_tolerance:
                mismatches += 1
        idx_list.append(k)
        signs.append(1 if s.direction == "long" else -1)
    idx = np.asarray(idx_list, dtype=np.int64)
    sg = np.asarray(signs, dtype=np.float64)
    rng = np.random.default_rng(seed)
    results: list[HorizonResult] = []
    for h in horizons:
        if len(idx) == 0:
            results.append(HorizonResult(h, 0, 0.0, 0.0, 1.0))
            continue
        r = _forward(series, idx, sg, h, round_trip_cost)
        observed = float(np.mean(r))
        candidates = np.arange(0, len(series) - max_h - 1)
        rand = rng.choice(candidates, size=(draws, len(idx)), replace=True)
        rand_means = np.asarray(
            [float(np.mean(_forward(series, row, sg, h, round_trip_cost))) for row in rand]
        )
        p = float((np.sum(rand_means >= observed) + 1) / (draws + 1))
        results.append(HorizonResult(h, len(idx), observed, float(np.mean(r > 0)), p))
    reasons: list[str] = []
    level = alpha / max(1, len(horizons))
    if len(idx) < min_events:
        reasons.append(f"only {len(idx)} matched signals (need {min_events})")
    best = min(results, key=lambda x: x.p_value)
    if best.p_value > level or best.mean_return <= 0:
        reasons.append(f"no horizon beats random timing after costs (best p={best.p_value:.3f})")
    if mismatches > 0.1 * max(1, len(idx)):
        reasons.append(f"{mismatches} signals disagree with the independent price")
    return SignalStudy(
        family,
        len(signals),
        len(idx),
        unmatched,
        mismatches,
        tuple(results),
        not reasons,
        tuple(reasons) or ("beats random timing out of sample",),
    )


__all__ = ["HorizonResult", "SignalStudy", "study"]
