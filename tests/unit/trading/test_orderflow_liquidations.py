"""Order flow and liquidation research: one source at a time, hints instead of
claims, modelled zones kept apart from observed liquidations."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from jarvis.trading.data import HOUR_MS, make_series
from jarvis.trading.instruments import BTC_USD
from jarvis.trading.liquidations import (
    ModelledZone,
    ObservedLiquidation,
    model_zones,
    nearest_targets,
)
from jarvis.trading.orderflow import (
    BookSnapshot,
    MixedSourcesError,
    absorption_hints,
    bar_delta,
    cvd,
    imbalance,
    wall_changes,
    walls,
)
from tests.fakes.fake_market import T0, random_walk


def _with_taker(n: int = 120, seed: int = 3, noise: float = 0.0):  # noqa: ANN202
    s = random_walk(n, seed=seed)
    share = 0.5 + np.random.default_rng(seed).normal(0, noise, n) if noise else 0.5
    return dataclasses.replace(s, source="binance:spot:BTCUSDT", taker_buy=s.volume * share)


def test_delta_and_cvd_need_a_venue_that_reports_taker_volume() -> None:
    s = _with_taker()
    buys = s.taker_buy.copy()
    buys[10] = s.volume[10]  # all aggressive buying
    s = dataclasses.replace(s, taker_buy=buys)
    d = bar_delta(s)
    assert d[10] == pytest.approx(s.volume[10]) and d[11] == pytest.approx(0.0)
    assert cvd(s)[-1] == pytest.approx(s.volume[10])
    with pytest.raises(ValueError):
        bar_delta(random_walk(10))  # no taker data: no invented delta


def _book(source: str = "bybit:spot:BTCUSDT", ts: int = 1, bid_wall: float = 50.0) -> BookSnapshot:
    bids = tuple((100.0 - k * 0.01, bid_wall if k == 5 else 1.0) for k in range(20))
    asks = tuple((100.01 + k * 0.01, 1.0) for k in range(20))
    return BookSnapshot(source, ts, bids, asks)


def test_book_imbalance_and_walls() -> None:
    book = _book()
    assert imbalance(book) > 0.5
    (wall,) = walls(book)
    assert wall.side == "bid" and wall.price == pytest.approx(99.95) and wall.multiple >= 5
    with pytest.raises(ValueError):
        BookSnapshot("x", 1, ((100.0, 1.0),), ((99.0, 1.0),))  # crossed


def test_vanishing_walls_are_hints_not_facts() -> None:
    before, after = _book(ts=1), _book(ts=2, bid_wall=1.0)
    (hint,) = wall_changes(before, after, traded_volume_near=0.5)
    assert hint.kind == "possible_spoofing" and hint.confidence == "low" and hint.caveats
    (eaten,) = wall_changes(before, after, traded_volume_near=45.0)
    assert eaten.kind == "liquidity_shift"  # traded away, not pulled
    with pytest.raises(MixedSourcesError):
        wall_changes(before, _book("okx:spot:BTC-USDT", ts=2), traded_volume_near=0)


def test_absorption_hint_needs_big_delta_and_a_flat_price() -> None:
    s = _with_taker(200, noise=0.03)
    o, c = s.open.copy(), s.close.copy()
    c[150] = o[150]  # flat bar
    buys = s.taker_buy.copy()
    buys[150] = s.volume[150] * 0.98
    s = dataclasses.replace(
        s, close=c, high=np.maximum(s.high, c), low=np.minimum(s.low, c), taker_buy=buys
    )
    hits = absorption_hints(s)
    assert any(i == 150 and h.kind == "possible_absorption" for i, h in hits)
    assert all(h.confidence in {"low", "medium"} for _i, h in hits)


def _bars_and_oi():  # noqa: ANN202
    rows = [(T0 + k * HOUR_MS, 100.0, 100.5, 99.5, 100.0, 10.0) for k in range(10)]
    s = make_series(BTC_USD, HOUR_MS, rows, source="bybit:perp:BTCUSDT")
    oi = [(T0 + k * HOUR_MS, 1000.0 + (100 if k >= 3 else 0)) for k in range(10)]
    return s, oi


def test_modelled_zones_are_labelled_with_their_assumptions() -> None:
    s, oi = _bars_and_oi()
    zones = model_zones(s, oi, oi_source="bybit:perp:BTCUSDT", leverage_mix={10: 1.0})
    assert {z.side for z in zones} == {"long", "short"}
    long_zone = next(z for z in zones if z.side == "long")
    assert long_zone.kind == "modelled" and long_zone.price_low < 90.6 < long_zone.price_high + 0.3
    assert any("leverage mix" in a for a in long_zone.assumptions)
    assert sum(z.est_notional for z in zones) == pytest.approx(100 * 100.0)
    targets = nearest_targets(zones, 100.0)
    assert targets["below"][0].side == "long" and targets["above"][0].side == "short"


def test_zones_vanish_when_price_trades_through_them_and_sources_cannot_mix() -> None:
    s, oi = _bars_and_oi()
    lows = s.low.copy()
    lows[6] = 85.0  # a flush through the long zone at ~90.5
    s = dataclasses.replace(s, low=lows)
    zones = model_zones(s, oi, oi_source="bybit:perp:BTCUSDT", leverage_mix={10: 1.0})
    assert {z.side for z in zones} == {"short"}
    with pytest.raises(ValueError):
        model_zones(s, oi, oi_source="okx:perp:BTC-USDT-SWAP")


def test_observed_and_modelled_are_different_types() -> None:
    seen = ObservedLiquidation("bybit:perp:BTCUSDT", T0, "long", 1.0, 90.0, "all liquidations")
    assert (
        seen.kind == "observed" and ModelledZone.__dataclass_fields__["kind"].default == "modelled"
    )
    assert not isinstance(seen, ModelledZone)
