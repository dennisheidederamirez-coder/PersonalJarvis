"""Order execution against a trade tape — the honest way for short holds.

Rules (all conservative; a fill is never better than the market allowed):

- **Latency:** an order submitted at ``t`` reaches the venue at
  ``t + latency_ms``; only trades AFTER that can fill it.
- **Spread:** with trades but no book, the touch is inferred from the
  aggressor side. A buyer-aggressor print happened at the ask, a
  seller-aggressor print at the bid; the other side is ``spread_bps`` away.
  A market buy pays the ask, a market sell gets the bid, plus ``impact_bps``.
- **Participation:** an order takes at most ``max_participation`` of each
  later trade's size. A large order fills over several trades, or only
  partly, and a market order stops after ``market_timeout_ms``.
- **Limit orders queue:** a resting buy at L fills only from sell-aggressor
  prints AT L after the volume queued ahead is used up, or when price
  trades THROUGH L (prints below L). A limit that would cross on arrival is
  marketable (taker) or, if post-only, rejected.
- **Venue limits:** at most ``max_orders_per_sec`` orders per second, plus
  an optional reject hook (maintenance, risk rejection, timeouts).
- **Fees:** taker fee on aggressive fills, maker fee on resting fills.

Each order is simulated independently against the tape (our own orders do
not interact); the order book itself is not reconstructed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from jarvis.trading.scalping.tape import TradeTape


@dataclass(frozen=True, slots=True)
class ExecConfig:
    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    latency_ms: int = 150
    spread_bps: float = 1.0
    impact_bps: float = 1.0
    max_participation: float = 0.2
    market_timeout_ms: int = 5_000
    max_orders_per_sec: int = 5
    #: when the queue ahead of a resting order is unknown: this many median trades
    default_queue_trades: float = 10.0


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    side: Literal["buy", "sell"]
    qty: float
    submitted_ms: int
    kind: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    post_only: bool = False
    expire_ms: int | None = None  # limits: cancelled at this time if not filled
    queue_ahead: float | None = None  # resting size ahead at our price, if known


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    ts_ms: int
    price: float
    qty: float
    fee: float
    liquidity: Literal["taker", "maker"]


@dataclass
class OrderResult:
    order: Order
    status: str  # filled / partial / cancelled / rejected / unfilled
    fills: list[Fill] = field(default_factory=list)
    reason: str = ""

    @property
    def filled_qty(self) -> float:
        return float(sum(f.qty for f in self.fills))

    @property
    def avg_price(self) -> float | None:
        q = self.filled_qty
        return sum(f.price * f.qty for f in self.fills) / q if q else None

    @property
    def fees(self) -> float:
        return float(sum(f.fee for f in self.fills))


def _touch(tape: TradeTape, k: int, cfg: ExecConfig) -> tuple[float, float]:
    """(bid, ask) implied by trade ``k`` and its aggressor side."""
    p = float(tape.price[k])
    s = cfg.spread_bps / 10_000
    if bool(tape.buyer_aggressor[k]):
        return p * (1 - s), p
    return p, p * (1 + s)


def _take(
    tape: TradeTape, start: int, order: Order, cfg: ExecConfig, cap: float | None, deadline: int
) -> list[Fill]:
    """Aggressive fills from trade ``start`` on (market, or a marketable limit)."""
    sign = 1 if order.side == "buy" else -1
    impact = cfg.impact_bps / 10_000
    fills: list[Fill] = []
    remaining = order.qty
    for k in range(start, len(tape)):
        t = int(tape.ts[k])
        if t > deadline or remaining <= 1e-12:
            break
        bid, ask = _touch(tape, k, cfg)
        price = (ask if sign > 0 else bid) * (1 + sign * impact)
        if cap is not None and sign * (price - cap) > 0:
            continue  # not executable within the limit at this moment
        take = min(remaining, float(tape.qty[k]) * cfg.max_participation)
        if take <= 0:
            continue
        fills.append(Fill(order.id, t, price, take, price * take * cfg.taker_fee, "taker"))
        remaining -= take
    return fills


def _rest(
    tape: TradeTape, start: int, order: Order, cfg: ExecConfig, deadline: int, queue: float
) -> list[Fill]:
    """Resting (maker) fills for a limit order from trade ``start`` on."""
    assert order.limit_price is not None
    lim = order.limit_price
    buy = order.side == "buy"
    fills: list[Fill] = []
    remaining = order.qty
    for k in range(start, len(tape)):
        t = int(tape.ts[k])
        if t > deadline or remaining <= 1e-12:
            break
        p, q = float(tape.price[k]), float(tape.qty[k])
        aggressor_sells = not bool(tape.buyer_aggressor[k])
        through = p < lim if buy else p > lim
        at_level = abs(p - lim) <= 1e-12 and (aggressor_sells if buy else not aggressor_sells)
        if through:
            # price went past our level: everything resting there, us included, traded
            take = min(remaining, q * cfg.max_participation)
        elif at_level:
            used = min(queue, q)
            queue -= used
            take = min(remaining, (q - used) * cfg.max_participation)
        else:
            continue
        if take > 0:
            fills.append(Fill(order.id, t, lim, take, lim * take * cfg.maker_fee, "maker"))
            remaining -= take
    return fills


def simulate(
    tape: TradeTape,
    orders: Sequence[Order],
    cfg: ExecConfig | None = None,
    *,
    reject: Callable[[Order], str | None] | None = None,
) -> list[OrderResult]:
    cfg = cfg or ExecConfig()
    median_qty = float(np.median(tape.qty)) if len(tape) else 0.0
    per_second: Counter[int] = Counter()
    results: list[OrderResult] = []
    for order in sorted(orders, key=lambda o: (o.submitted_ms, o.id)):
        if order.qty <= 0:
            results.append(OrderResult(order, "rejected", reason="non-positive quantity"))
            continue
        second = order.submitted_ms // 1000
        per_second[second] += 1
        if per_second[second] > cfg.max_orders_per_sec:
            results.append(OrderResult(order, "rejected", reason="rate limited"))
            continue
        venue = reject(order) if reject else None
        if venue:
            results.append(OrderResult(order, "rejected", reason=venue))
            continue
        arrival = order.submitted_ms + cfg.latency_ms
        start = int(np.searchsorted(tape.ts, arrival, side="left"))
        if order.kind == "market":
            fills = _take(tape, start, order, cfg, None, arrival + cfg.market_timeout_ms)
            results.append(_result(order, fills, "insufficient liquidity after arrival"))
            continue
        if order.limit_price is None:
            results.append(OrderResult(order, "rejected", reason="limit order without a price"))
            continue
        deadline = order.expire_ms if order.expire_ms is not None else int(2**62)
        last = start - 1
        marketable = False
        if last >= 0:
            bid, ask = _touch(tape, last, cfg)
            marketable = (
                (order.limit_price >= ask) if order.side == "buy" else (order.limit_price <= bid)
            )
        if marketable and order.post_only:
            results.append(OrderResult(order, "rejected", reason="post-only order would cross"))
            continue
        if marketable:
            fills = _take(
                tape,
                start,
                order,
                cfg,
                order.limit_price,
                min(deadline, arrival + cfg.market_timeout_ms),
            )
        else:
            queue = (
                order.queue_ahead
                if order.queue_ahead is not None
                else cfg.default_queue_trades * median_qty
            )
            fills = _rest(tape, start, order, cfg, deadline, queue)
        results.append(_result(order, fills, "expired before a (full) fill"))
    return results


def _result(order: Order, fills: list[Fill], why_short: str) -> OrderResult:
    filled = sum(f.qty for f in fills)
    if filled >= order.qty - 1e-12:
        return OrderResult(order, "filled", fills)
    if filled > 0:
        return OrderResult(order, "partial", fills, why_short)
    return OrderResult(
        order, "unfilled" if order.kind == "market" else "cancelled", fills, why_short
    )


__all__ = ["ExecConfig", "Fill", "Order", "OrderResult", "simulate"]
