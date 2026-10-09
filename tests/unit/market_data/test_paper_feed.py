"""The paper feed: cache only by default, cross-checked, closed bars only;
incremental updates fetch each closed bar once."""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path

from jarvis.core.http_pool import HttpClientPool
from jarvis.market_data.adapters import ADAPTERS
from jarvis.market_data.client import PoliteClient
from jarvis.market_data.paper_feed import StoreProvider, update
from jarvis.market_data.paper_step import main
from jarvis.market_data.store import BarStore
from jarvis.trading.data import DAY_MS, HOUR_MS
from jarvis.trading.instruments import BTC_USD
from jarvis.trading.paper_spec import DEFAULT_SPEC
from tests.fakes.fake_exchanges import FakeExchanges

T0 = 1_767_225_600_000
NOW = T0 + 500 * DAY_MS + 2 * HOUR_MS + 600_000  # inside a 4h bar
STREAM_A = DEFAULT_SPEC.streams[0]  # A:BTC-USD, daily
STREAM_B = DEFAULT_SPEC.streams[-1]  # B:BTC-USD, 4h


async def _sleep(_s: float) -> None:
    return None


def _adapters(fake: FakeExchanges) -> dict:  # type: ignore[type-arg]
    return {
        n: ADAPTERS[n](
            PoliteClient(
                n,
                min_interval_s=0,
                pool=HttpClientPool(transport=fake.transport()),
                sleep=_sleep,
                rng=random.Random(1),  # noqa: S311 - test jitter
            ),
            now_ms=lambda: NOW,
        )  # noqa: S311
        for n in ("binance", "okx")
    }


async def test_update_fetches_new_closed_bars_once_then_nothing(tmp_path: Path) -> None:
    fake = FakeExchanges(NOW)
    store = BarStore(tmp_path / "md.sqlite")
    first = await update(DEFAULT_SPEC, store, _adapters(fake), NOW)
    assert not first.errors and first.fetched
    calls = len(fake.calls)
    again = await update(DEFAULT_SPEC, store, _adapters(fake), NOW)
    assert again.fetched == {} and len(fake.calls) == calls  # nothing new: no request
    last = store.last_ts("binance:perp:BTCUSDT", STREAM_B.interval_ms)
    assert last is not None and last + STREAM_B.interval_ms <= NOW  # no forming bar stored


async def test_the_provider_uses_the_cache_crosschecks_and_passes_funding(tmp_path: Path) -> None:
    fake = FakeExchanges(NOW)
    store = BarStore(tmp_path / "md.sqlite")
    await update(DEFAULT_SPEC, store, _adapters(fake), NOW)
    data = StoreProvider(store)(STREAM_B, NOW)
    assert data.ok and data.series is not None and data.series.source == STREAM_B.primary_source
    assert int(data.series.ts[-1]) + STREAM_B.interval_ms <= NOW
    assert data.funding  # observed funding of the same venue

    # a backup that disagrees by 0.5 % on every bar: no decision on this data
    okx = store.load(STREAM_B.backup_source, BTC_USD, STREAM_B.interval_ms)
    store.save(
        dataclasses.replace(
            okx,
            open=okx.open * 1.005,
            high=okx.high * 1.005,
            low=okx.low * 1.005,
            close=okx.close * 1.005,
        )
    )
    disputed = StoreProvider(store)(STREAM_B, NOW)
    assert not disputed.ok and "disagree" in disputed.reason


def test_the_manual_step_runs_offline(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    store = BarStore(tmp_path / "md.sqlite")  # empty cache, no --fetch: no network at all
    assert main(["--store", str(store.path), "--journal", str(tmp_path / "paper.sqlite")]) == 0
    out = capsys.readouterr().out
    assert "simulation only" in out and "no data" in out or "skipped" in out
