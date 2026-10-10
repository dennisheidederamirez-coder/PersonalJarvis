"""Book calibration: observed spread/depth, walked market orders, modelled maker fills."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jarvis.market_data.recorder import RecorderConfig, RecordStore, record
from jarvis.trading.scalping.bookcal import (
    Book,
    book_stats,
    load_recording,
    maker_fill_experiment,
    market_order_cost,
)
from jarvis.trading.scalping.tape import TradeTape
from tests.fakes.fake_market_stream import Connector, ScriptedConn, agg, depth

T0 = 1_790_000_000_000


def book(ts: int, mid: float = 100.0, size: float = 2.0) -> Book:
    return Book(
        ts,
        tuple((mid - 0.05 - 0.1 * i, size) for i in range(20)),
        tuple((mid + 0.05 + 0.1 * i, size) for i in range(20)),
    )


def test_spread_and_depth_are_observed_values() -> None:
    st = book_stats([book(T0), book(T0 + 500)])
    assert st["provenance"] == "observed" and st["snapshots"] == 2
    assert st["spread_bps"]["median"] == pytest.approx(10.0)  # 0.10 on 100
    assert st["depth_notional_median"]["bid_10bp"] == pytest.approx(99.95 * 2)


def test_a_market_order_walks_the_book() -> None:
    small = market_order_cost([book(T0)], 50.0)
    assert small["cost_bps"]["median"] == pytest.approx(5.0)  # half spread
    big = market_order_cost([book(T0)], 1_000.0)  # needs several levels
    assert big["cost_bps"]["median"] > 5.0
    huge = market_order_cost([book(T0)], 1e9)
    assert huge["beyond_top20"] == 2 and huge["cost_bps"]["median"] is None


def test_maker_fills_need_the_queue_to_clear_and_report_adverse_selection() -> None:
    books = [book(T0 + k * 1_000, mid=100.0 - 0.01 * k) for k in range(400)]
    rows = []
    for k in range(400):  # sellers hit the bid every second, the price drifts down
        rows.append((T0 + k * 1_000 + 300, 99.95 - 0.01 * k, 0.8, False))
    tape = TradeTape.from_rows("t", rows)
    r = maker_fill_experiment(
        books, tape, every_ms=10_000, ttl_ms=60_000, notional=50.0, horizon_ms=30_000
    )
    assert r["provenance"].startswith("modelled") and r["orders"] > 0
    assert 0 < r["fill_rate"] + r["partial_rate"] <= 1
    assert r["adverse_bps_after_fill_mean"] < 0  # filled buys were followed by a falling price


async def test_end_to_end_from_a_recording(tmp_path: Path) -> None:
    t = {"now": 0.0}

    def tick() -> None:
        t["now"] += 1.0

    def msg(n: int) -> str:
        ts = T0 + n * 100
        return depth("BTCUSDT", ts) if n % 2 else agg("BTCUSDT", ts, n)

    store = RecordStore(tmp_path / "r.sqlite")

    async def no_sleep(_s: float) -> None:
        return None

    await record(
        RecorderConfig(duration_s=50),
        store,
        Connector(
            public=[ScriptedConn(lambda n: depth("BTCUSDT", T0 + n * 200), tick)],
            market=[ScriptedConn(lambda n: agg("BTCUSDT", T0 + n * 200 + 100, n), tick)],
        ),
        clock=lambda: t["now"],
        sleep=no_sleep,
    )
    store.close()
    books, tape = load_recording(tmp_path / "r.sqlite", "BTCUSDT")
    assert len(books) == len(tape) == 25 and np.all(np.diff(tape.ts) >= 0)
    assert book_stats(books)["spread_bps"]["median"] == pytest.approx(10.0, rel=0.01)


def test_fill_bounds_from_the_book_alone() -> None:
    from jarvis.trading.scalping.bookcal import liquidity_over_time, maker_fill_bounds

    # price falls 0.1 every second: a resting bid is first emptied, then crossed
    falling = [book(T0 + k * 500, mid=100.0 - 0.05 * k) for k in range(600)]
    r = maker_fill_bounds(falling, every_ms=10_000, ttl_ms=10_000, horizon_ms=5_000)
    assert r["provenance"].startswith("observed book, bounds only")
    assert r["fill_rate_lower_bound"] == pytest.approx(0.5)  # every bid gets crossed, no ask does
    assert r["fill_rate_upper_bound"] == pytest.approx(0.5)
    assert r["adverse_bps_after_sure_fill_mean"] < 0  # bids filled into a falling market
    flat = [book(T0 + k * 500) for k in range(600)]
    f = maker_fill_bounds(flat, every_ms=10_000, ttl_ms=10_000, horizon_ms=5_000)
    assert f["fill_rate_lower_bound"] == 0 and f["fill_rate_upper_bound"] == 0
    rows = liquidity_over_time(flat, bucket_ms=60_000)
    assert sum(r["snapshots"] for r in rows) == 600  # T0 is not minute-aligned: 6 buckets
    assert len(rows) == 6 and all(r["spread_bps_median"] > 0 for r in rows)
