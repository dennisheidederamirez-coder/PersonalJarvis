"""Plan first, then fetch: request counts, time and storage are known before
any download; funding and open interest land in their own tables."""

from __future__ import annotations

import random

from jarvis.core.http_pool import HttpClientPool
from jarvis.market_data.adapters import ADAPTERS, Kind
from jarvis.market_data.backfill import Job, plan, run
from jarvis.market_data.client import PoliteClient
from jarvis.market_data.store import BarStore
from jarvis.trading.data import DAY_MS, HOUR_MS
from jarvis.trading.instruments import BTC_USD
from tests.fakes.fake_exchanges import FakeExchanges

T0 = 1_767_225_600_000
NOW = T0 + 400 * DAY_MS


async def _sleep(_s: float) -> None:
    return None


def _adapters(fake: FakeExchanges) -> dict:  # type: ignore[type-arg]
    out = {}
    for name in ("bybit", "okx", "coinbase"):
        client = PoliteClient(
            name,
            min_interval_s=0,
            pool=HttpClientPool(transport=fake.transport()),
            sleep=_sleep,
            rng=random.Random(1),  # noqa: S311 - test jitter
        )
        out[name] = ADAPTERS[name](client, now_ms=lambda: NOW)
    return out


def test_the_plan_counts_requests_time_and_storage() -> None:
    year = 365 * DAY_MS
    p = plan(
        [
            Job(
                "bybit",
                BTC_USD,
                Kind.PERP,
                HOUR_MS,
                T0,
                T0 + year,
                funding=True,
                open_interest=True,
            ),
            Job("okx", BTC_USD, Kind.PERP, HOUR_MS, T0, T0 + year),
        ]
    )
    assert p.bars == 2 * 8760
    assert p.requests == {"bybit": 9 + 6 + 44, "okx": 88}
    assert 0 < p.est_mb < 5 and p.est_seconds > 0


async def test_run_stores_bars_funding_and_oi_and_reports_coverage(tmp_path) -> None:  # noqa: ANN001
    fake = FakeExchanges(NOW, listed_from=T0 + 10 * DAY_MS)  # listed later than asked
    store = BarStore(tmp_path / "md.sqlite")
    results = await run(
        [
            Job(
                "bybit",
                BTC_USD,
                Kind.PERP,
                HOUR_MS,
                T0,
                T0 + 30 * DAY_MS,
                funding=True,
                open_interest=True,
            ),
            Job("coinbase", BTC_USD, Kind.SPOT, 4 * HOUR_MS, T0, T0 + DAY_MS),  # no 4h at Coinbase
        ],
        _adapters(fake),
        store,
    )
    ok, missing = results
    assert ok.coverage is not None and ok.coverage.first_ms == T0 + 10 * DAY_MS  # not filled in
    assert ok.coverage.bars == 20 * 24 and ok.error == ""
    assert len(store.load_funding("bybit:perp:BTCUSDT")) == 60
    assert len(store.load_open_interest("bybit:perp:BTCUSDT")) == 20 * 24
    assert missing.coverage is None and "240 min bars" in missing.error
    assert store.sources()[0][0] == "bybit:perp:BTCUSDT"
