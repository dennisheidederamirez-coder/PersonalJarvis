"""Trade-level execution: latency, spread, participation, queues, partial
fills, post-only, rejections, fees — never an instant ideal fill."""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.trading.data import HOUR_MS
from jarvis.trading.instruments import BTC_USD
from jarvis.trading.scalping.microsim import ExecConfig, Order, simulate
from jarvis.trading.scalping.tape import TradeTape, to_bars

T0 = 1_767_225_600_000
CFG = ExecConfig(
    taker_fee=0.0005,
    maker_fee=0.0002,
    latency_ms=100,
    spread_bps=2.0,
    impact_bps=1.0,
    max_participation=0.5,
    market_timeout_ms=2_000,
)


def tape(rows: list[tuple[int, float, float, bool]]) -> TradeTape:
    return TradeTape.from_rows("binance:perp:BTCUSDT", [(T0 + t, p, q, b) for t, p, q, b in rows])


def test_latency_means_only_later_trades_can_fill() -> None:
    t = tape([(0, 100.0, 1, True), (50, 99.0, 1, True), (150, 101.0, 4, True)])
    (r,) = simulate(t, [Order("m", "buy", 1.0, T0)], CFG)
    assert r.status == "filled" and r.fills[0].ts_ms == T0 + 150  # not the cheaper print at +50
    assert r.avg_price == pytest.approx(101.0 * 1.0001)


def test_a_market_buy_pays_the_ask_even_after_a_print_at_the_bid() -> None:
    t = tape([(200, 100.0, 10, False)])  # seller-aggressor: printed at the bid
    (r,) = simulate(t, [Order("m", "buy", 1.0, T0)], CFG)
    assert r.avg_price == pytest.approx(100.0 * 1.0002 * 1.0001)  # ask = bid + 2 bps, + impact
    assert r.fees == pytest.approx(r.avg_price * 1.0 * 0.0005) and r.fills[0].liquidity == "taker"
    (s,) = simulate(t, [Order("s", "sell", 1.0, T0)], CFG)
    assert s.avg_price == pytest.approx(100.0 * (1 - 0.0001))  # the bid, minus impact


def test_participation_and_timeout_give_partial_fills() -> None:
    t = tape([(200, 100.0, 1, True), (400, 100.0, 1, True), (5_000, 100.0, 9, True)])
    (r,) = simulate(t, [Order("m", "buy", 2.0, T0)], CFG)
    assert r.status == "partial" and r.filled_qty == pytest.approx(1.0)  # 0.5 + 0.5, then timeout
    assert len(r.fills) == 2 and "liquidity" in r.reason


def test_a_resting_limit_waits_for_its_queue() -> None:
    t = tape(
        [(0, 100.5, 1, True), (200, 100.0, 3, False), (300, 100.0, 2, True), (400, 100.0, 4, False)]
    )
    order = Order("l", "buy", 1.0, T0, "limit", 100.0, queue_ahead=4.0)
    (r,) = simulate(t, [order], CFG)
    # 3 sold into the queue (4 ahead -> 1 left), a buy print at 100 fills no bid,
    # then 4 more: 1 clears the queue, 3 remain, half of that (participation) is ours
    assert r.status == "filled" and r.fills[0].ts_ms == T0 + 400
    assert r.fills[0].price == 100.0 and r.fills[0].liquidity == "maker"
    assert r.fees == pytest.approx(100.0 * 1.0 * 0.0002)


def test_trading_through_the_level_fills_and_expiry_cancels() -> None:
    through = tape([(0, 101.0, 1, True), (200, 99.5, 4, False)])
    (r,) = simulate(through, [Order("l", "buy", 1.0, T0, "limit", 100.0, queue_ahead=50)], CFG)
    assert r.status == "filled" and r.avg_price == 100.0  # never better than our limit
    calm = tape([(0, 101.0, 1, True), (200, 100.8, 4, False), (9_000, 99.0, 4, False)])
    (c,) = simulate(calm, [Order("l", "buy", 1.0, T0, "limit", 100.0, expire_ms=T0 + 5_000)], CFG)
    assert c.status == "cancelled" and c.fills == []


def test_post_only_rejected_when_crossing_marketable_limit_capped() -> None:
    t = tape([(0, 100.0, 1, True), (200, 100.0, 4, True), (300, 102.0, 4, True)])
    (p,) = simulate(t, [Order("p", "buy", 1.0, T0, "limit", 100.5, post_only=True)], CFG)
    assert p.status == "rejected" and "post-only" in p.reason
    (m,) = simulate(t, [Order("m", "buy", 1.0, T0, "limit", 100.5)], CFG)
    assert m.status == "filled" and all(f.price <= 100.5 for f in m.fills)


def test_rate_limit_and_venue_rejections() -> None:
    t = tape([(200, 100.0, 100, True)])
    orders = [Order(f"o{k}", "buy", 0.1, T0 + k) for k in range(7)]
    results = simulate(t, orders, CFG)
    assert [r.status for r in results].count("rejected") == 2  # 5 per second allowed
    (r,) = simulate(t, [Order("x", "buy", 0.1, T0)], CFG, reject=lambda o: "venue maintenance")
    assert r.status == "rejected" and r.reason == "venue maintenance"


def test_bars_from_trades_keep_aggressor_volume_and_never_invent_gaps() -> None:
    rows = [
        (0, 100.0, 1, True),
        (30_000, 101.0, 2, False),
        (180_000, 99.0, 1, True),
        (240_500, 98.0, 3, True),
    ]
    t = tape(rows)
    bars = to_bars(t, BTC_USD, 60_000, until_ms=T0 + 240_000)
    assert len(bars) == 2  # minute 0 and minute 3; minutes 1-2 had no trade; minute 4 still open
    assert bars.close[0] == 101.0 and bars.volume[0] == 3.0 and bars.taker_buy[0] == 1.0
    with pytest.raises(ValueError):
        TradeTape(
            "x",
            np.array([2, 1]),
            np.array([1.0, 1.0]),
            np.array([1.0, 1.0]),
            np.array([True, True]),
        )
    assert len(to_bars(t, BTC_USD, HOUR_MS)) == 1
