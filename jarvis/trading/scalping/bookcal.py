"""Execution calibration from recorded order-book snapshots plus trades.

Every number says where it comes from:

- ``observed``: read straight from the recorded book (spread, depth);
- ``observed book, modelled consumption``: a market order walked through a
  recorded book snapshot. The book is real; that it stays put while our
  order executes is an assumption;
- ``modelled``: maker fills. A hypothetical limit order joins the BACK of
  the displayed queue, and recorded trades must use up the queue before it
  fills (``microsim``). This is evidence about fill CHANCES under that
  assumption, NOT an observed fill: our own order never existed. Hidden or
  iceberg liquidity and cancellations ahead of us are unknown, so real fills
  could come sooner (queue cancellations) or later (hidden size ahead).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import numpy as np

from jarvis.trading.scalping.microsim import ExecConfig, Order, simulate
from jarvis.trading.scalping.tape import TradeTape

_BUY: Final[Literal["buy"]] = "buy"
_SELL: Final[Literal["sell"]] = "sell"


@dataclass(frozen=True)
class Book:
    ts: int
    bids: tuple[tuple[float, float], ...]  # best first
    asks: tuple[tuple[float, float], ...]

    @property
    def mid(self) -> float:
        return (self.bids[0][0] + self.asks[0][0]) / 2

    @property
    def spread_bps(self) -> float:
        return (self.asks[0][0] - self.bids[0][0]) / self.mid * 10_000


def load_recording(path: str | Path, symbol: str) -> tuple[list[Book], TradeTape]:
    db = sqlite3.connect(path)
    try:
        books = [
            Book(
                int(ts),
                tuple((float(p), float(q)) for p, q in json.loads(b)),
                tuple((float(p), float(q)) for p, q in json.loads(a)),
            )
            for ts, b, a in db.execute(
                "SELECT event_ms, bids, asks FROM rec_book WHERE symbol = ? ORDER BY event_ms",
                (symbol,),
            )
        ]
        rows = [
            (int(t), float(p), float(q), bool(b))
            for t, p, q, b in db.execute(
                "SELECT ts, price, qty, buyer_aggressor FROM rec_trades"
                " WHERE symbol = ? ORDER BY ts, agg_id",
                (symbol,),
            )
        ]
    finally:
        db.close()
    return [b for b in books if b.bids and b.asks], TradeTape.from_rows(
        f"binance:perp:{symbol}", rows
    )


def _pct(x: Sequence[float], q: float) -> float | None:
    return float(np.percentile(np.asarray(x), q)) if len(x) else None


def book_stats(books: Sequence[Book], bands_bps: Sequence[float] = (1, 5, 10)) -> dict[str, object]:
    """Observed: spread and resting notional within each band of the mid."""
    spreads = [b.spread_bps for b in books]
    depth: dict[str, list[float]] = {}
    for b in books:
        m = b.mid
        for band in bands_bps:
            lo, hi = m * (1 - band / 10_000), m * (1 + band / 10_000)
            depth.setdefault(f"bid_{band:g}bp", []).append(sum(p * q for p, q in b.bids if p >= lo))
            depth.setdefault(f"ask_{band:g}bp", []).append(sum(p * q for p, q in b.asks if p <= hi))
    return {
        "provenance": "observed",
        "snapshots": len(books),
        "spread_bps": {
            "median": _pct(spreads, 50),
            "p90": _pct(spreads, 90),
            "max": max(spreads) if spreads else None,
        },
        "depth_notional_median": {k: _pct(v, 50) for k, v in depth.items()},
    }


def market_order_cost(books: Sequence[Book], notional: float) -> dict[str, object]:
    """A market BUY of ``notional`` walked through each snapshot: average
    price against the mid (bps), half spread included. Sells mirror it."""
    costs: list[float] = []
    unfilled = 0
    for b in books:
        for levels, sign in ((b.asks, 1), (b.bids, -1)):
            left, spent, got = notional, 0.0, 0.0
            for p, q in levels:
                take = min(left, p * q)
                spent += take
                got += take / p
                left -= take
                if left <= 1e-9:
                    break
            if left > 1e-9:
                unfilled += 1
                continue
            costs.append(sign * (spent / got / b.mid - 1) * 10_000)
    return {
        "provenance": "observed book, modelled consumption",
        "notional": notional,
        "cost_bps": {
            "median": _pct(costs, 50),
            "p90": _pct(costs, 90),
            "max": max(costs) if costs else None,
        },
        "beyond_top20": unfilled,
    }


def maker_fill_experiment(
    books: Sequence[Book],
    tape: TradeTape,
    *,
    every_ms: int = 10_000,
    ttl_ms: int = 60_000,
    notional: float = 10_000.0,
    horizon_ms: int = 60_000,
    cfg: ExecConfig | None = None,
) -> dict[str, object]:
    """Hypothetical post-only orders at the best bid and ask, sampled every
    ``every_ms``, each at the back of the displayed queue. They fill from
    recorded trades only. Adverse selection: the mid move over
    ``horizon_ms`` after a fill, in the order's direction, against the same
    move after every placement."""
    cfg = cfg or ExecConfig(latency_ms=150, max_participation=1.0)
    if not books or len(tape) == 0:
        return {"provenance": "modelled", "orders": 0}
    ts = np.asarray([b.ts for b in books], dtype=np.int64)
    mids = np.asarray([b.mid for b in books])
    orders: list[Order] = []
    meta: dict[str, tuple[int, float]] = {}
    next_t = int(ts[0])
    for k, b in enumerate(books):
        if b.ts < next_t or b.ts + ttl_ms + horizon_ms > int(ts[-1]):
            continue
        next_t = b.ts + every_ms
        for side, (px, q_ahead) in ((_BUY, b.bids[0]), (_SELL, b.asks[0])):
            oid = f"{side}{k}"
            orders.append(
                Order(
                    oid,
                    side,
                    notional / px,
                    b.ts,
                    "limit",
                    px,
                    post_only=True,
                    expire_ms=b.ts + ttl_ms,
                    queue_ahead=q_ahead,
                )
            )
            meta[oid] = (k, px)
    results = simulate(tape, orders, cfg)

    def mid_at(t: int) -> float:
        return float(mids[max(0, int(np.searchsorted(ts, t, side="right")) - 1)])

    fills, partial, ttf, adv_fill, adv_all = 0, 0, [], [], []
    for r in results:
        k, px = meta[r.order.id]
        sign = 1 if r.order.side == "buy" else -1
        adv_all.append(sign * (mid_at(books[k].ts + horizon_ms) / mid_at(books[k].ts) - 1) * 10_000)
        if r.fills:
            first = r.fills[0].ts_ms
            ttf.append((first - books[k].ts) / 1000)
            adv_fill.append(sign * (mid_at(first + horizon_ms) / mid_at(first) - 1) * 10_000)
            if r.status == "filled":
                fills += 1
            else:
                partial += 1
    n = len(results)
    return {
        "provenance": "modelled (back-of-queue assumption, recorded trades)",
        "orders": n,
        "fill_rate": fills / n if n else 0.0,
        "partial_rate": partial / n if n else 0.0,
        "rejected": sum(1 for r in results if r.status == "rejected"),
        "time_to_first_fill_s_median": _pct(ttf, 50),
        "adverse_bps_after_fill_mean": float(np.mean(adv_fill)) if adv_fill else None,
        "move_bps_after_any_placement_mean": float(np.mean(adv_all)) if adv_all else None,
        "ttl_s": ttl_ms / 1000,
        "horizon_s": horizon_ms / 1000,
    }


def maker_fill_bounds(
    books: Sequence[Book], *, every_ms: int = 10_000, ttl_ms: int = 60_000, horizon_ms: int = 60_000
) -> dict[str, object]:
    """Bounds on the fill chance of a post-only order at the best bid (ask),
    from BOOK SNAPSHOTS alone — no trades, no queue model:

    - lower bound: within ``ttl`` the best ask fell to or below our bid. A
      resting order at that price MUST have traded, or the book would have
      been crossed;
    - upper bound: within ``ttl`` the best bid fell below our price. Our
      level was emptied, by trades (a fill is possible) or by cancellations
      (no fill). Anything less, and the order surely did not fill.

    Snapshots every 500 ms can miss brief moves, so the lower bound is
    conservative. ``adverse``: the mid move over ``horizon`` after the
    sure-fill moment, in the order's direction, against the move after any
    placement."""
    if len(books) < 2:
        return {"provenance": "observed book, bounds only", "orders": 0}
    ts = np.asarray([b.ts for b in books], dtype=np.int64)
    bid = np.asarray([b.bids[0][0] for b in books])
    ask = np.asarray([b.asks[0][0] for b in books])
    mid = (bid + ask) / 2
    sure, maybe, n = 0, 0, 0
    adv_sure: list[float] = []
    adv_all: list[float] = []

    def mid_at(t: int) -> float:
        return float(mid[max(0, int(np.searchsorted(ts, t, side="right")) - 1)])

    next_t = int(ts[0])
    for k in range(len(books)):
        if ts[k] < next_t or ts[k] + ttl_ms + horizon_ms > ts[-1]:
            continue
        next_t = int(ts[k]) + every_ms
        hi = int(np.searchsorted(ts, ts[k] + ttl_ms, side="right"))
        for sign, price in ((1, bid[k]), (-1, ask[k])):
            n += 1
            adv_all.append(sign * (mid_at(int(ts[k]) + horizon_ms) / mid[k] - 1) * 10_000)
            window = slice(k + 1, hi)
            crossed = (ask[window] <= price) if sign > 0 else (bid[window] >= price)
            emptied = (bid[window] < price) if sign > 0 else (ask[window] > price)
            if crossed.any():
                sure += 1
                t_fill = int(ts[k + 1 + int(np.argmax(crossed))])
                adv_sure.append(sign * (mid_at(t_fill + horizon_ms) / mid_at(t_fill) - 1) * 10_000)
            if crossed.any() or emptied.any():
                maybe += 1
    return {
        "provenance": "observed book, bounds only (no fill observed)",
        "orders": n,
        "fill_rate_lower_bound": sure / n if n else 0.0,
        "fill_rate_upper_bound": maybe / n if n else 0.0,
        "adverse_bps_after_sure_fill_mean": float(np.mean(adv_sure)) if adv_sure else None,
        "move_bps_after_any_placement_mean": float(np.mean(adv_all)) if adv_all else None,
        "ttl_s": ttl_ms / 1000,
        "horizon_s": horizon_ms / 1000,
    }


def liquidity_over_time(
    books: Sequence[Book], bucket_ms: int = 300_000, band_bps: float = 5.0
) -> list[dict[str, float]]:
    """Observed: per time bucket, median spread and resting notional within
    ``band_bps`` of the mid on each side."""
    out: dict[int, list[Book]] = {}
    for b in books:
        out.setdefault(b.ts // bucket_ms * bucket_ms, []).append(b)
    rows = []
    for start, bs in sorted(out.items()):
        st = book_stats(bs, (band_bps,))
        depth = st["depth_notional_median"]
        assert isinstance(depth, dict)
        spread = st["spread_bps"]
        assert isinstance(spread, dict)
        rows.append(
            {
                "start_ms": float(start),
                "snapshots": float(len(bs)),
                "spread_bps_median": float(spread["median"] or 0.0),
                f"bid_{band_bps:g}bp": float(depth[f"bid_{band_bps:g}bp"] or 0.0),
                f"ask_{band_bps:g}bp": float(depth[f"ask_{band_bps:g}bp"] or 0.0),
            }
        )
    return rows


__all__ = [
    "Book",
    "book_stats",
    "liquidity_over_time",
    "load_recording",
    "maker_fill_bounds",
    "maker_fill_experiment",
    "market_order_cost",
]
