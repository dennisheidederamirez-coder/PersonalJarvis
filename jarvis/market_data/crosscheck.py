"""Compare independent sources; choose ONE whole series; refuse when unsure.

Two series are compared bar by bar on their common timestamps (close vs.
close). Spot and perpetual prices differ by the basis and are not compared
unless explicitly allowed; USD and USDT quotes differ slightly, so the
tolerance is in relative terms. The result never blends prices: it names
the series to use, or none.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from jarvis.trading.data import BarSeries, assess


@dataclass(frozen=True)
class Comparison:
    a: str
    b: str
    common_bars: int
    median_dev: float  # relative |close_a / close_b - 1|
    p99_dev: float
    max_dev: float
    disagreements: int  # bars beyond the tolerance


@dataclass(frozen=True)
class Choice:
    use: str | None  # the source to use, or None: do not trade on this data
    reasons: tuple[str, ...] = field(default_factory=tuple)
    comparison: Comparison | None = None


def _kind(source: str) -> str:
    parts = source.split(":")
    return parts[1] if len(parts) > 2 else ""


def compare(
    a: BarSeries, b: BarSeries, *, tolerance: float = 0.003, allow_cross_kind: bool = False
) -> Comparison:
    if a.interval_ms != b.interval_ms:
        raise ValueError("different intervals")
    if not allow_cross_kind and _kind(a.source) != _kind(b.source):
        raise ValueError("spot and perpetual prices are not compared without opt-in")
    common, ia, ib = np.intersect1d(a.ts, b.ts, return_indices=True)
    if len(common) == 0:
        return Comparison(a.source, b.source, 0, 0.0, 0.0, 0.0, 0)
    dev = np.abs(a.close[ia] / b.close[ib] - 1)
    return Comparison(
        a.source,
        b.source,
        len(common),
        float(np.median(dev)),
        float(np.quantile(dev, 0.99)),
        float(np.max(dev)),
        int(np.sum(dev > tolerance)),
    )


def choose(
    primary: BarSeries | None,
    backup: BarSeries | None,
    *,
    now_ms: int,
    max_age_bars: int = 2,
    tolerance: float = 0.003,
    max_disagreement: float = 0.01,
) -> Choice:
    """The primary if it is fresh, usable and agrees with the backup; else the
    backup if IT is fresh and usable; else nothing."""

    def fresh(s: BarSeries | None) -> tuple[bool, str]:
        if s is None or len(s) == 0:
            return False, "no data"
        q = assess(s)
        if q.grade in ("unusable", "poor"):
            return False, f"quality {q.grade}"
        age = (now_ms - int(s.ts[-1]) - s.interval_ms) // s.interval_ms
        if age > max_age_bars:
            return False, f"stale by {age} bars"
        return True, ""

    reasons: list[str] = []
    p_ok, p_why = fresh(primary)
    b_ok, b_why = fresh(backup)
    cmp = None
    if p_ok and b_ok and primary is not None and backup is not None:
        cmp = compare(primary, backup, tolerance=tolerance)
        share = cmp.disagreements / cmp.common_bars if cmp.common_bars else 1.0
        if share > max_disagreement:
            return Choice(None, (f"sources disagree on {share:.1%} of bars",), cmp)
        return Choice(primary.source, ("primary fresh and confirmed by backup",), cmp)
    if p_ok and primary is not None:
        return Choice(primary.source, (f"backup unavailable: {b_why}",))
    reasons.append(f"primary unavailable: {p_why}")
    if b_ok and backup is not None:
        return Choice(backup.source, (*reasons, "using the backup"))
    reasons.append(f"backup unavailable: {b_why}")
    return Choice(None, tuple(reasons))


__all__ = ["Choice", "Comparison", "choose", "compare"]
