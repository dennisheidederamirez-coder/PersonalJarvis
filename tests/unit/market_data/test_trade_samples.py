"""Time-boxed aggTrades samples: paging, budget cap, storage, calibration."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jarvis.core.http_pool import HttpClientPool
from jarvis.market_data.client import PoliteClient
from jarvis.market_data.trades import BudgetExceeded, TradeStore, Window, fetch_window, plan
from jarvis.trading.scalping.calibrate import calibrate, effective_spread_bps
from tests.fakes.fake_agg_trades import FakeAggTrades

T0 = 1_790_000_000_000


def market(n: int, seed: int = 1, every_ms: int = 100) -> list[tuple[int, float, float, bool]]:
    rng = np.random.default_rng(seed)
    out, mid = [], 80_000.0
    for k in range(n):
        mid *= 1 + rng.normal(0, 0.00002)
        buyer_maker = bool(rng.random() < 0.5)
        px = mid * (1 - 0.00005) if buyer_maker else mid * (1 + 0.00005)  # 1 bp spread
        out.append((T0 + k * every_ms, round(px, 1), float(rng.uniform(0.001, 0.5)), buyer_maker))
    return out


def client(fake: FakeAggTrades) -> PoliteClient:
    async def no_sleep(_s: float) -> None:
        return None

    return PoliteClient(
        "binance",
        min_interval_s=0.0,
        sleep=no_sleep,
        pool=HttpClientPool(transport=fake.transport()),
    )


async def test_a_window_pages_by_id_and_stops_at_its_end() -> None:
    fake = FakeAggTrades(market(3_500))
    c = client(fake)
    tape, complete = await fetch_window(c, Window("BTCUSDT", T0 + 1_000, T0 + 250_000), budget=10)
    assert complete
    assert len(tape) == 2_490 and int(tape.ts[0]) == T0 + 1_000 and int(tape.ts[-1]) < T0 + 250_000
    assert c.requests == 3 and "startTime" in fake.calls[0] and "fromId" in fake.calls[1]
    assert tape.buyer_aggressor.sum() == sum(1 for t in market(3_500)[10:2_500] if not t[3])


async def test_the_budget_stops_the_run() -> None:
    c = client(FakeAggTrades(market(5_000)))
    with pytest.raises(BudgetExceeded):
        await fetch_window(c, Window("BTCUSDT", T0, T0 + 499_000), budget=2)
    assert c.requests == 2
    c2 = client(FakeAggTrades(market(5_000)))
    tape, complete = await fetch_window(
        c2, Window("BTCUSDT", T0, T0 + 499_000), budget=2, partial_ok=True
    )
    assert not complete and len(tape) == 2_000  # the trades so far, marked as truncated


def test_windows_are_shorter_than_an_hour_and_planned() -> None:
    with pytest.raises(ValueError):
        Window("BTCUSDT", T0, T0 + 3_600_000)
    p = plan([Window("BTCUSDT", T0, T0 + 1_800_000)] * 4, {"BTCUSDT": 30_000})
    assert p["requests"] == 4 * 15 and p["weight"] == 4 * 15 * 20 and p["trades"] == 60_000


async def test_store_round_trip_and_calibration(tmp_path: Path) -> None:
    tape, _ = await fetch_window(
        client(FakeAggTrades(market(20_000, every_ms=20))),
        Window("BTCUSDT", T0, T0 + 400_000),
        budget=50,
    )
    store = TradeStore(tmp_path / "t.sqlite")
    assert store.save(tape) == len(tape) and store.save(tape) == len(tape)  # idempotent
    back = store.load(tape.source, T0, T0 + 400_000)
    assert len(back) == len(tape) and np.array_equal(back.price, tape.price)
    assert effective_spread_bps(back) == pytest.approx(1.0, abs=0.3)
    cal = calibrate(back, notional=5_000, every_ms=10_000)
    assert cal.trades == len(back) and cal.fill_rate > 0.9
    assert cal.entry_cost_bps is not None and cal.entry_cost_bps[0] > 0  # a taker pays
    assert cal.round_trip_bps is not None and cal.round_trip_bps[0] > 10  # incl. 2 x 5 bp fees
    assert set(cal.drift_bps) == {150, 500, 1000}
