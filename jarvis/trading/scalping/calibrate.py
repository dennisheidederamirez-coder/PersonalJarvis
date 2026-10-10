"""Cost calibration from a trade tape.

Measures what a taker actually meets, instead of assuming it:

- **effective spread:** buyer- and seller-aggressor prints close together
  in time straddle the touch; their gap estimates the quoted spread;
- **latency drift:** how far price moves between a decision and the first
  trade after the latency — the part of slippage that is pure delay;
- **market-order cost:** market orders of a given notional, submitted at
  regular decision times and executed by ``microsim``, compared with the
  last price known at decision time (what a bar-close backtest would use).

The result is a round-trip cost estimate with percentiles, to check the
research assumption (0.15 %) against.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from jarvis.trading.scalping.microsim import ExecConfig, Order, simulate
from jarvis.trading.scalping.tape import TradeTape


@dataclass(frozen=True)
class Calibration:
    source: str
    hours: float
    trades: int
    trades_per_hour: float
    spread_bps_median: float | None
    drift_bps: dict[int, tuple[float, float]]  # latency ms -> (median, p90) absolute move
    entry_cost_bps: tuple[float, float, float] | None  # median, p90, max per market order
    fill_rate: float  # share of market orders filled completely
    round_trip_bps: tuple[float, float] | None  # (median, p90) incl. two taker fees

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "hours": round(self.hours, 2),
            "trades": self.trades,
            "trades_per_hour": round(self.trades_per_hour),
            "spread_bps_median": _r(self.spread_bps_median),
            "drift_bps": {str(k): [_r(a), _r(b)] for k, (a, b) in self.drift_bps.items()},
            "entry_cost_bps": [_r(x) for x in self.entry_cost_bps] if self.entry_cost_bps else None,
            "fill_rate": round(self.fill_rate, 3),
            "round_trip_bps": [_r(x) for x in self.round_trip_bps] if self.round_trip_bps else None,
        }


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 3)


def effective_spread_bps(tape: TradeTape, max_gap_ms: int = 50) -> float | None:
    """Median gap between neighbouring opposite-aggressor prints (bps)."""
    if len(tape) < 2:
        return None
    gaps = np.diff(tape.ts)
    opposite = tape.buyer_aggressor[1:] != tape.buyer_aggressor[:-1]
    close = (gaps <= max_gap_ms) & opposite
    if not np.any(close):
        return None
    a, b = tape.price[:-1][close], tape.price[1:][close]
    buy_px = np.where(tape.buyer_aggressor[1:][close], b, a)
    sell_px = np.where(tape.buyer_aggressor[1:][close], a, b)
    mid = (buy_px + sell_px) / 2
    return float(np.median((buy_px - sell_px) / mid * 10_000))


def latency_drift_bps(tape: TradeTape, latency_ms: int) -> tuple[float, float]:
    """(median, p90) of |price move| from each trade to the first trade at
    least ``latency_ms`` later."""
    idx = np.searchsorted(tape.ts, tape.ts + latency_ms, side="left")
    ok = idx < len(tape)
    if not np.any(ok):
        return (float("nan"), float("nan"))
    move = np.abs(tape.price[idx[ok]] / tape.price[ok] - 1) * 10_000
    return float(np.median(move)), float(np.percentile(move, 90))


def calibrate(
    tape: TradeTape,
    *,
    notional: float = 10_000.0,
    every_ms: int = 60_000,
    cfg: ExecConfig | None = None,
) -> Calibration:
    n = len(tape)
    hours = (int(tape.ts[-1]) - int(tape.ts[0])) / 3_600_000 if n > 1 else 0.0
    spread = effective_spread_bps(tape)
    cfg = cfg or ExecConfig()
    if spread is not None:
        cfg = replace(cfg, spread_bps=max(spread, 0.0))
    drift = {lat: latency_drift_bps(tape, lat) for lat in (150, 500, 1000)} if n > 1 else {}
    costs: list[float] = []
    full = 0
    total = 0
    if n > 1:
        start, stop = int(tape.ts[0]) + every_ms, int(tape.ts[-1]) - cfg.market_timeout_ms
        orders = []
        refs: dict[str, float] = {}
        for k, t in enumerate(range(start, stop, every_ms)):
            last = int(np.searchsorted(tape.ts, t, side="right")) - 1
            if last < 0:
                continue
            ref = float(tape.price[last])
            qty = notional / ref
            for side in ("buy", "sell"):
                oid = f"{side}{k}"
                refs[oid] = ref
                # spaced so the per-second rate limit never binds here
                orders.append(Order(oid, side, qty, t + (0 if side == "buy" else 500)))
        for res in simulate(tape, orders, cfg):
            total += 1
            if res.status == "filled":
                full += 1
            if res.avg_price is not None:
                ref = refs[res.order.id]
                sign = 1 if res.order.side == "buy" else -1
                costs.append(sign * (res.avg_price / ref - 1) * 10_000)
    entry = (
        (float(np.median(costs)), float(np.percentile(costs, 90)), float(np.max(costs)))
        if costs
        else None
    )
    fee_bps = cfg.taker_fee * 10_000
    rt = (2 * entry[0] + 2 * fee_bps, 2 * entry[1] + 2 * fee_bps) if entry else None
    return Calibration(
        tape.source,
        hours,
        n,
        n / hours if hours else 0.0,
        spread,
        drift,
        entry,
        full / total if total else 0.0,
        rt,
    )


__all__ = ["Calibration", "calibrate", "effective_spread_bps", "latency_drift_bps"]
