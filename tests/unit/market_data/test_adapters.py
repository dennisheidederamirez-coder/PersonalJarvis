"""Venue adapters against a faithful fake: paging, ordering, open bars,
provenance, retries, politeness — and no network."""

from __future__ import annotations

import dataclasses
import random

import numpy as np
import pytest

from jarvis.core.http_pool import HttpClientPool
from jarvis.market_data.adapters import ADAPTERS, Adapter, Kind
from jarvis.market_data.client import MarketDataError, PoliteClient
from jarvis.market_data.crosscheck import choose, compare
from jarvis.market_data.store import BarStore
from jarvis.trading.data import HOUR_MS, assess
from jarvis.trading.instruments import BTC_USD, ETH_USD
from tests.fakes.fake_exchanges import FakeExchanges

T0 = 1_767_225_600_000  # 2026-01-01T00:00Z
NOW = T0 + 3000 * HOUR_MS + 30 * 60_000  # half-way through bar 3000


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


def _adapter(name: str, fake: FakeExchanges, sleeps: Sleeps | None = None) -> Adapter:
    client = PoliteClient(
        name,
        min_interval_s=0.0,
        pool=HttpClientPool(transport=fake.transport()),
        sleep=sleeps or Sleeps(),
        rng=random.Random(1),  # noqa: S311 - deterministic jitter in tests
    )
    return ADAPTERS[name](client, now_ms=lambda: NOW)


@pytest.mark.parametrize("venue", ["bybit", "okx", "binance", "coinbase"])
async def test_every_venue_pages_through_history_and_drops_the_open_bar(venue: str) -> None:
    fake = FakeExchanges(NOW)
    adapter = _adapter(venue, fake)
    kind = Kind.SPOT if venue == "coinbase" else Kind.PERP
    series = await adapter.bars(BTC_USD, kind, HOUR_MS, T0, T0 + 3001 * HOUR_MS)
    assert len(series) == 3000  # bar 3000 is still open
    assert int(series.ts[0]) == T0 and int(series.ts[-1]) == T0 + 2999 * HOUR_MS
    assert np.all(np.diff(series.ts) == HOUR_MS)
    assert assess(series).grade == "good"
    assert series.source.startswith(f"{venue}:{kind}:")
    assert len(fake.calls) == -(-3001 // adapter.page_bars)  # one request per page


async def test_only_binance_reports_aggressive_buy_volume() -> None:
    fake = FakeExchanges(NOW)
    b = await _adapter("binance", fake).bars(BTC_USD, Kind.SPOT, HOUR_MS, T0, T0 + 50 * HOUR_MS)
    y = await _adapter("bybit", fake).bars(BTC_USD, Kind.SPOT, HOUR_MS, T0, T0 + 50 * HOUR_MS)
    assert b.taker_buy is not None and np.allclose(b.taker_buy, b.volume * 0.6)
    assert y.taker_buy is None


async def test_symbols_follow_each_venue() -> None:
    fake = FakeExchanges(NOW)
    assert _adapter("okx", fake).symbol(ETH_USD, Kind.PERP) == "ETH-USDT-SWAP"
    assert _adapter("bybit", fake).symbol(ETH_USD, Kind.SPOT) == "ETHUSDT"
    assert _adapter("coinbase", fake).symbol(ETH_USD, Kind.SPOT) == "ETH-USD"
    with pytest.raises(MarketDataError):
        await _adapter("coinbase", fake).bars(BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + HOUR_MS)


@pytest.mark.parametrize("venue", ["bybit", "okx", "binance"])
async def test_funding_history(venue: str) -> None:
    fake = FakeExchanges(NOW)
    points = await _adapter(venue, fake).funding(BTC_USD, T0, T0 + 2400 * HOUR_MS)
    assert len(points) == 300 and points[0].ts_ms == T0
    assert all(b.ts_ms - a.ts_ms == 8 * HOUR_MS for a, b in zip(points, points[1:], strict=False))


async def test_open_interest_history() -> None:
    fake = FakeExchanges(NOW)
    oi = await _adapter("bybit", fake).open_interest(BTC_USD, T0, T0 + 500 * HOUR_MS)
    assert len(oi) == 500 and oi[0].unit == "base"
    with pytest.raises(MarketDataError):
        await _adapter("okx", fake).open_interest(BTC_USD, T0, T0 + HOUR_MS)


async def test_retries_are_bounded_jittered_and_respect_retry_after() -> None:
    fake = FakeExchanges(NOW)
    sleeps = Sleeps()
    fake.fail_next = [429, 503]
    fake.retry_after = "7"
    s = await _adapter("bybit", fake, sleeps).bars(
        BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + 10 * HOUR_MS
    )
    assert len(s) == 10 and sleeps.calls[:2] == [7.0, 7.0]

    fake.fail_next, fake.retry_after = [500, 500, 500, 500], None
    with pytest.raises(MarketDataError) as err:
        await _adapter("bybit", fake, Sleeps()).bars(BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + HOUR_MS)
    assert "secret provider detail" not in str(err.value)  # provider bodies never surface

    fake.fail_next = [403]
    with pytest.raises(MarketDataError):  # a refusal is not retried
        await _adapter("okx", fake, Sleeps()).bars(BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + HOUR_MS)
    assert fake.fail_next == []


async def test_requests_are_spaced() -> None:
    fake = FakeExchanges(NOW)
    sleeps = Sleeps()
    ticks = iter(range(1000))
    client = PoliteClient(
        "okx",
        min_interval_s=0.25,
        pool=HttpClientPool(transport=fake.transport()),
        sleep=sleeps,
        clock=lambda: next(ticks) * 0.01,
    )
    await ADAPTERS["okx"](client, now_ms=lambda: NOW).bars(
        BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + 300 * HOUR_MS
    )
    assert len(sleeps.calls) >= 2 and all(0 < s <= 0.25 for s in sleeps.calls)


async def test_cache_round_trip_keeps_sources_apart(tmp_path) -> None:  # noqa: ANN001
    fake = FakeExchanges(NOW)
    store = BarStore(tmp_path / "bars.sqlite")
    b = await _adapter("binance", fake).bars(BTC_USD, Kind.SPOT, HOUR_MS, T0, T0 + 100 * HOUR_MS)
    y = await _adapter("bybit", fake).bars(BTC_USD, Kind.SPOT, HOUR_MS, T0, T0 + 100 * HOUR_MS)
    store.save(b)
    store.save(y)
    store.save(b)  # idempotent
    again = store.load(b.source, BTC_USD, HOUR_MS)
    assert len(again) == 100 and np.allclose(again.close, b.close)
    assert again.taker_buy is not None and store.load(y.source, BTC_USD, HOUR_MS).taker_buy is None
    assert store.last_ts(b.source, HOUR_MS) == T0 + 99 * HOUR_MS


async def test_crosscheck_compares_but_never_blends() -> None:
    fake = FakeExchanges(NOW)
    by = await _adapter("bybit", fake).bars(BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + 3001 * HOUR_MS)
    ok = await _adapter("okx", fake).bars(BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + 3001 * HOUR_MS)
    cmp = compare(by, ok)
    assert cmp.common_bars == 3000 and cmp.median_dev == pytest.approx(0.0002, rel=0.05)
    choice = choose(by, ok, now_ms=NOW)
    assert choice.use == by.source
    spot = await _adapter("binance", fake).bars(BTC_USD, Kind.SPOT, HOUR_MS, T0, T0 + 100 * HOUR_MS)
    with pytest.raises(ValueError):
        compare(by, spot)  # perp vs spot needs an explicit opt-in

    stale = choose(by.slice(0, 2000), ok, now_ms=NOW)
    assert stale.use == ok.source and "stale" in stale.reasons[0]
    neither = choose(None, ok.slice(0, 10), now_ms=NOW)
    assert neither.use is None


async def test_disagreeing_sources_mean_no_data_to_trade_on() -> None:
    fake = FakeExchanges(NOW)
    by = await _adapter("bybit", fake).bars(BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + 3001 * HOUR_MS)
    ok = await _adapter("okx", fake).bars(BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + 3001 * HOUR_MS)
    # e.g. a wrong contract mapped: every price 0.5 % off, each bar plausible on its own
    shifted = dataclasses.replace(
        ok, open=ok.open * 1.005, high=ok.high * 1.005, low=ok.low * 1.005, close=ok.close * 1.005
    )
    choice = choose(by, shifted, now_ms=NOW)
    assert choice.use is None and "disagree" in choice.reasons[0]
