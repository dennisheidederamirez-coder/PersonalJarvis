"""Liquidations: what HAPPENED versus what a model ESTIMATES — two types that
cannot be confused.

``ObservedLiquidation`` is an event a venue reported. Its ``coverage`` says
how complete that venue's public feed is (some publish every liquidation,
some only the largest per second).

``ModelledZone`` is an estimate of where leveraged positions WOULD be
liquidated. It is built only from public, free inputs (bars and open
interest of ONE venue) and a stated set of assumptions about leverage. It is
a research map, not knowledge of other traders' positions. Commercial
heatmaps (e.g. CoinGlass) use their own, undisclosed models.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

import numpy as np

from jarvis.trading.data import BarSeries

#: How complete each venue's public liquidation feed is (2026-10 docs).
FEED_COVERAGE: Final[dict[str, str]] = {
    "bybit": "all liquidations (allLiquidation topic, 500 ms batches)",
    "binance": "only the largest liquidation per symbol per second (forceOrder)",
    "okx": "at most one update per contract per second",
}


@dataclass(frozen=True, slots=True)
class ObservedLiquidation:
    source: str  # "<venue>:perp:<symbol>"
    ts_ms: int
    liquidated: Literal["long", "short"]
    qty: float
    price: float
    coverage: str
    kind: Literal["observed"] = "observed"


@dataclass(frozen=True)
class ModelledZone:
    side: Literal["long", "short"]  # which positions would be liquidated here
    price_low: float
    price_high: float
    est_notional: float  # model units: quote value of open interest assumed there
    source: str
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    kind: Literal["modelled"] = "modelled"


DEFAULT_LEVERAGE_MIX: Final[dict[float, float]] = {5: 0.2, 10: 0.35, 25: 0.25, 50: 0.15, 100: 0.05}


def model_zones(
    series: BarSeries,
    oi: Sequence[tuple[int, float]],
    *,
    oi_source: str,
    leverage_mix: dict[float, float] | None = None,
    mmr: float = 0.005,
    bin_pct: float = 0.0025,
    long_share: float = 0.5,
) -> list[ModelledZone]:
    """Estimate liquidation zones from open-interest INCREASES.

    Assumptions (all stated on every zone):
    - new open interest in a bar was opened at that bar's close;
    - it splits ``long_share`` / rest into longs / shorts (default 50/50);
    - leverage follows ``leverage_mix``;
    - open-interest decreases close positions proportionally everywhere;
    - a zone is removed once price has traded through it (those positions
      would already be liquidated).
    """
    if not series.source.split(":")[0] == oi_source.split(":")[0]:
        raise ValueError("bars and open interest must come from the same venue")
    mix = leverage_mix or DEFAULT_LEVERAGE_MIX
    if abs(sum(mix.values()) - 1) > 1e-9:
        raise ValueError("leverage mix must sum to 1")
    pos = {int(t): k for k, t in enumerate(series.ts)}
    book: dict[tuple[str, int], float] = {}
    prev_oi: float | None = None
    last_close = float(series.close[-1]) if len(series) else 0.0
    for ts, value in sorted(oi):
        k = pos.get(int(ts))
        if k is None:
            continue
        close = float(series.close[k])
        if prev_oi is not None and prev_oi > 0:
            change = value - prev_oi
            if change > 0:
                notional = change * close
                for lev, w in mix.items():
                    for side, share, sign in (
                        ("long", long_share, -1),
                        ("short", 1 - long_share, 1),
                    ):
                        liq = close * (1 + sign * (1 / lev - mmr))
                        b = int(np.floor(np.log(liq) / np.log1p(bin_pct)))
                        book[(side, b)] = book.get((side, b), 0.0) + notional * w * share
            elif change < 0:
                keep = value / prev_oi
                book = {key: v * keep for key, v in book.items()}
        hi, lo = float(series.high[k]), float(series.low[k])
        for key in list(book):
            side, b = key
            price = np.exp(b * np.log1p(bin_pct))
            if (side == "long" and lo <= price) or (side == "short" and hi >= price):
                del book[key]
        prev_oi = value
    assumptions = (
        "new open interest opened at the bar close",
        f"long share {long_share:.0%}",
        "leverage mix " + ", ".join(f"{k:g}x {v:.0%}" for k, v in sorted(mix.items())),
        "decreases close positions proportionally",
        f"one venue only: {oi_source}",
    )
    out = []
    for (side, b), notional in sorted(book.items(), key=lambda kv: kv[0][1]):
        lo_p, hi_p = np.exp(b * np.log1p(bin_pct)), np.exp((b + 1) * np.log1p(bin_pct))
        if (side == "long" and hi_p > last_close) or (side == "short" and lo_p < last_close):
            continue
        out.append(ModelledZone(side, float(lo_p), float(hi_p), notional, oi_source, assumptions))  # type: ignore[arg-type]
    return out


def nearest_targets(
    zones: Sequence[ModelledZone], price: float, n: int = 3
) -> dict[str, list[ModelledZone]]:
    """The largest modelled zones below (long liquidations) and above (short
    liquidations) the price — possible liquidity targets, nothing more."""
    below = sorted(
        (z for z in zones if z.side == "long" and z.price_high <= price),
        key=lambda z: -z.est_notional,
    )[:n]
    above = sorted(
        (z for z in zones if z.side == "short" and z.price_low >= price),
        key=lambda z: -z.est_notional,
    )[:n]
    return {"below": below, "above": above}


__all__ = [
    "DEFAULT_LEVERAGE_MIX",
    "FEED_COVERAGE",
    "ModelledZone",
    "ObservedLiquidation",
    "model_zones",
    "nearest_targets",
]
