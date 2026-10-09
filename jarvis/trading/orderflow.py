"""Order-flow research: delta / CVD, order-book imbalance, liquidity walls and
their changes — with patterns reported as HINTS, never as facts.

Every input is scoped to ONE venue and ONE market kind (spot or perpetual):
spot and futures flow differ (futures carry leverage, liquidations and
funding-driven flow), and books of different venues are different markets.
Functions refuse to combine sources.

These are research inputs. A strategy may use one only after it has been
validated like any other rule; no trade depends on them by default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.data import BarSeries

FloatArray = NDArray[np.float64]


class MixedSourcesError(ValueError):
    """Inputs from different venues or market kinds were combined."""


# ------------------------------------------------------------------ trade flow


def bar_delta(series: BarSeries) -> FloatArray:
    """Aggressive buys minus aggressive sells per bar (base units). Needs a
    venue that reports taker-buy volume per bar (e.g. Binance klines)."""
    if series.taker_buy is None:
        raise ValueError(f"{series.source} reports no taker-buy volume")
    return 2.0 * series.taker_buy - series.volume


def cvd(series: BarSeries) -> FloatArray:
    """Cumulative volume delta from the first bar of *series* (one source)."""
    return np.cumsum(bar_delta(series))


# ------------------------------------------------------------------ order book


@dataclass(frozen=True)
class BookSnapshot:
    source: str  # "<venue>:<kind>:<symbol>"
    ts_ms: int
    bids: tuple[tuple[float, float], ...]  # (price, size), best first
    asks: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not self.bids or not self.asks:
            raise ValueError("empty side")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("crossed book")
        if any(a[0] < b[0] for a, b in zip(self.bids, self.bids[1:], strict=False)):
            raise ValueError("bids not sorted best first")
        if any(a[0] > b[0] for a, b in zip(self.asks, self.asks[1:], strict=False)):
            raise ValueError("asks not sorted best first")

    @property
    def mid(self) -> float:
        return (self.bids[0][0] + self.asks[0][0]) / 2


def imbalance(book: BookSnapshot, *, band: float = 0.005) -> float:
    """(bid size - ask size) / total within +/- *band* of the mid, -1..1.
    Visible size only — resting orders can be pulled at any time."""
    lo, hi = book.mid * (1 - band), book.mid * (1 + band)
    bid = sum(q for p, q in book.bids if p >= lo)
    ask = sum(q for p, q in book.asks if p <= hi)
    return 0.0 if bid + ask == 0 else (bid - ask) / (bid + ask)


@dataclass(frozen=True, slots=True)
class Wall:
    side: str  # bid / ask
    price: float
    size: float
    multiple: float  # size / median level size on that side


def walls(book: BookSnapshot, *, min_multiple: float = 5.0) -> list[Wall]:
    """Levels much larger than the side's median level (visible liquidity zones)."""
    out: list[Wall] = []
    for side, levels in (("bid", book.bids), ("ask", book.asks)):
        sizes = np.asarray([q for _p, q in levels])
        median = float(np.median(sizes)) if len(sizes) else 0.0
        if median <= 0:
            continue
        out += [Wall(side, p, q, q / median) for p, q in levels if q / median >= min_multiple]
    return out


@dataclass(frozen=True)
class Hint:
    """A pattern that MAY be present. Not a fact; evidence and caveats attached."""

    kind: str  # possible_absorption / possible_spoofing / liquidity_shift
    confidence: str  # always "low" or "medium" — never "certain"
    evidence: str
    caveats: tuple[str, ...] = field(default_factory=tuple)


def wall_changes(
    before: BookSnapshot,
    after: BookSnapshot,
    *,
    traded_volume_near: float,
    min_multiple: float = 5.0,
    tolerance: float = 0.0005,
) -> list[Hint]:
    """Compare walls between two snapshots of the SAME book.

    A wall that vanished while little traded near its price is reported as a
    *possible* spoofing hint (it may as well have been cancelled for honest
    reasons); a wall that appeared is a liquidity shift."""
    if before.source != after.source:
        raise MixedSourcesError("snapshots from different books")
    if after.ts_ms <= before.ts_ms:
        raise ValueError("snapshots out of order")
    old, new = walls(before, min_multiple=min_multiple), walls(after, min_multiple=min_multiple)

    def near(w: Wall, pool: Sequence[Wall]) -> bool:
        return any(o.side == w.side and abs(o.price / w.price - 1) <= tolerance for o in pool)

    hints: list[Hint] = []
    for w in old:
        if not near(w, new):
            pulled = traded_volume_near < 0.25 * w.size
            hints.append(
                Hint(
                    "possible_spoofing" if pulled else "liquidity_shift",
                    "low",
                    f"{w.side} wall {w.size:g} at {w.price:g} gone; "
                    f"{traded_volume_near:g} traded near it",
                    (
                        "orders are cancelled for many legitimate reasons",
                        "a single venue's visible book, not the whole market",
                    ),
                )
            )
    for w in new:
        if not near(w, old):
            hints.append(
                Hint(
                    "liquidity_shift",
                    "low",
                    f"new {w.side} wall {w.size:g} at {w.price:g}",
                    ("visible size can be pulled at any time",),
                )
            )
    return hints


def absorption_hints(
    series: BarSeries,
    *,
    z: float = 2.5,
    lookback: int = 50,
    max_move: float = 0.001,
) -> list[tuple[int, Hint]]:
    """Bars with an unusually large delta but almost no price move: aggressive
    flow that MAY have been absorbed by passive orders."""
    delta = bar_delta(series)
    move = series.close / series.open - 1
    out: list[tuple[int, Hint]] = []
    for i in range(lookback, len(series)):
        window = delta[i - lookback : i]
        sd = float(np.std(window))
        if sd <= 0:
            continue
        score = (delta[i] - float(np.mean(window))) / sd
        if abs(score) >= z and abs(move[i]) <= max_move:
            side = "buying" if score > 0 else "selling"
            out.append(
                (
                    i,
                    Hint(
                        "possible_absorption",
                        "low",
                        f"aggressive {side} {abs(score):.1f} sd above normal, "
                        f"price moved {move[i]:.3%}",
                        (
                            "bar data hides the order of events inside the bar",
                            f"one venue only: {series.source}",
                        ),
                    ),
                )
            )
    return out


__all__ = [
    "BookSnapshot",
    "Hint",
    "MixedSourcesError",
    "Wall",
    "absorption_hints",
    "bar_delta",
    "cvd",
    "imbalance",
    "wall_changes",
    "walls",
]
