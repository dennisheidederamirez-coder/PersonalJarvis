"""Scalping candidates, levels, controller, profiles and splits."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from jarvis.trading.data import DAY_MS, BarSeries, make_series
from jarvis.trading.engine import DemoTrader
from jarvis.trading.instruments import BTC_USD, ETH_USD
from jarvis.trading.journal import MemoryJournal
from jarvis.trading.paper import PaperBroker
from jarvis.trading.risk import RiskManager
from jarvis.trading.scalping.controller import ControllerLimits, PortfolioController
from jarvis.trading.scalping.levels import levels
from jarvis.trading.scalping.profiles import (
    CONSERVATIVE,
    CONSERVATIVE_SCALP,
    DEMO_10X_5PCT,
    Approval,
    ProfileLocked,
    resolve,
)
from jarvis.trading.scalping.splits import TestAlreadyUsed, three_way
from jarvis.trading.scalping.strategies import EmaTrendScalp, OrderflowLevelScalp, ScalpBreakout
from jarvis.trading.strategies import Side

M1 = 60_000
T0 = 1_767_225_600_000  # 2026-01-01 00:00 UTC, a Thursday


def minutes(
    n: int,
    seed: int = 1,
    *,
    taker: bool = True,
    vol: float = 0.0008,
    surge_every: int = 0,
    instrument: Any = BTC_USD,
) -> BarSeries:
    rng = np.random.default_rng(seed)
    closes = 30_000 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    rows, buys = [], []
    prev = closes[0]
    for k, c in enumerate(closes):
        o = prev
        v = float(rng.uniform(5, 15)) * (6 if surge_every and k % surge_every == 0 else 1)
        rows.append((T0 + k * M1, o, max(o, c) * 1.0003, min(o, c) * 0.9997, float(c), v))
        buys.append(v * float(rng.uniform(0.2, 0.8)))
        prev = c
    return make_series(
        instrument,
        M1,
        rows,
        source="binance:perp:BTCUSDT@trades",
        taker_buy=buys if taker else None,
    )


@pytest.mark.parametrize("make", [ScalpBreakout, EmaTrendScalp, OrderflowLevelScalp])
def test_scalp_decisions_never_use_future_bars(make: Any) -> None:
    s = minutes(3 * 1440, seed=2, surge_every=37)
    full, cut = make(), make()
    full.prepare(s)
    cut.prepare(s.slice(0, 2 * 1440))
    for i in range(2 * 1440):
        for cur in (None, Side.LONG):
            assert full.decide(i, cur) == cut.decide(i, cur)
            assert full.explain(i, cur) == cut.explain(i, cur)


def test_breakout_needs_a_volume_surge() -> None:
    quiet, loud = minutes(2000, seed=3), minutes(2000, seed=3, surge_every=13)
    a, b = ScalpBreakout(), ScalpBreakout()
    a.prepare(quiet)
    b.prepare(loud)
    assert sum(a.decide(i, None) is not None for i in range(2000)) < sum(
        b.decide(i, None) is not None for i in range(2000)
    )
    reasons = {a.explain(i, None).split(" (")[0] for i in range(30, 2000)}
    assert any(r.startswith("range break without volume") for r in reasons)


def test_ema_trend_and_orderflow_explain_themselves() -> None:
    s = minutes(3000, seed=4, surge_every=29)
    e, o = EmaTrendScalp(), OrderflowLevelScalp()
    e.prepare(s)
    o.prepare(s)
    assert e.explain(10, None) == "warming up"
    assert any("trend" in e.explain(i, None) for i in range(300, 3000))
    texts = {o.explain(i, None) for i in range(2000, 3000)}
    assert texts & {"no PVSRA climax bar", "climax bar away from every reference level"}
    no_taker = OrderflowLevelScalp()
    no_taker.prepare(minutes(500, seed=4, taker=False))
    assert all(no_taker.decide(i, None) is None for i in range(500))
    assert no_taker.explain(100, None) == "no aggressor volume from this source"


def test_a_time_stop_closes_stale_scalps() -> None:
    s = minutes(3000, seed=5, surge_every=11)
    strat = ScalpBreakout(max_hold=5)
    strat.prepare(s)
    first = next(i for i in range(3000) if strat.decide(i, None) is not None)
    later = strat.decide(first + 5, Side.LONG if strat._sig[first] > 0 else Side.SHORT)
    assert later is not None and later.side is None and "time stop" in later.reason


def test_levels_come_from_completed_periods_only() -> None:
    s = minutes(9 * 1440, seed=6)
    lv = levels(s)
    assert np.isnan(lv.prev_day_high[100])  # day 1: no completed day yet
    d2 = 1440 + 5
    assert lv.prev_day_high[d2] == pytest.approx(float(s.high[:1440].max()))
    assert lv.daily_open[d2] == pytest.approx(float(s.open[1440]))
    monday = 4 * 1440  # 2026-01-05 is a Monday; the week before is incomplete
    nxt = 11 * 1440 if 11 * 1440 < len(s) else None
    assert np.isnan(lv.prev_week_high[monday + 10])
    cut = levels(s.slice(0, 5 * 1440))
    for k in range(5 * 1440):
        for name in ("daily_open", "prev_day_high", "prev_week_low"):
            a, b = getattr(lv, name)[k], getattr(cut, name)[k]
            assert (np.isnan(a) and np.isnan(b)) or a == b
    assert nxt is None


def test_the_controller_refuses_and_never_loosens() -> None:
    c = PortfolioController(
        20_000,
        ControllerLimits(
            max_entries_per_day=3,
            max_entries_per_day_per_strategy=2,
            loss_streak=2,
            cooldown_ms=10 * M1,
            max_concurrent=2,
            max_exposure_frac=1.0,
        ),
    )
    now = T0
    assert c.check_entry("s1", "BTC-USD", "long", 5_000, now) == []
    c.on_open("s1", "BTC-USD", "long", 5_000, now)
    assert "correlated position" in " ".join(c.check_entry("s2", "ETH-USD", "long", 5_000, now))
    assert c.check_entry("s2", "ETH-USD", "short", 5_000, now) == []  # opposite: allowed
    c2 = PortfolioController(20_000, correlations={frozenset(("BTC-USD", "ETH-USD")): 0.3})
    c2.on_open("s1", "BTC-USD", "long", 5_000, now)
    assert c2.check_entry("s2", "ETH-USD", "long", 5_000, now) == []  # known low correlation
    assert "exposure" in " ".join(c.check_entry("s2", "ETH-USD", "short", 16_000, now))
    c.on_close("s1", "BTC-USD", -10.0, now)
    c.on_open("s1", "BTC-USD", "long", 1_000, now)
    c.on_close("s1", "BTC-USD", -10.0, now)  # two losses in a row: cooldown
    assert "cooling down" in " ".join(c.check_entry("s1", "BTC-USD", "long", 1, now))
    assert "strategy entry limit" in " ".join(c.check_entry("s1", "SOL-USD", "long", 1, now))
    c.on_open("s3", "SOL-USD", "short", 1, now)
    assert "portfolio entry limit" in " ".join(c.check_entry("s4", "XRP-USD", "long", 1, now))
    c.on_close("s3", "SOL-USD", -400.0, now)
    assert "daily loss" in " ".join(c.check_entry("s4", "XRP-USD", "long", 1, now))
    assert c.check_entry("s4", "XRP-USD", "long", 1, now + DAY_MS) == []  # a new UTC day


def test_the_controller_vetoes_inside_the_engine() -> None:
    s = minutes(3000, seed=7, surge_every=9)
    strat = ScalpBreakout(name="scalp")
    strat.prepare(s)
    ctrl = PortfolioController(10_000, ControllerLimits(max_entries_per_day=1))
    journal = MemoryJournal()
    limits, _grant = resolve(CONSERVATIVE_SCALP)
    trader = DemoTrader(
        PaperBroker(10_000),
        RiskManager(limits),
        journal,
        entry_guard=ctrl.check_entry,
        on_open=lambda st, f: ctrl.on_open(st, f.symbol, f.side.value, f.price * f.qty, f.ts_ms),
        on_trade=lambda t: ctrl.on_close(t.strategy, t.symbol, t.net, t.exit_ms),
    )
    for i in range(len(s)):
        trader.process_bar(s, i, strat)
    fills_per_day: dict[int, int] = {}
    for d in journal.kinds("fill"):
        if d["action"] == "open":
            fills_per_day[d["ts_ms"] // DAY_MS] = fills_per_day.get(d["ts_ms"] // DAY_MS, 0) + 1
    assert fills_per_day and max(fills_per_day.values()) == 1
    assert any(r.startswith("portfolio: ") for d in journal.kinds("rejected") for r in d["reasons"])


def test_profiles_conservative_is_free_the_demo_profile_is_locked() -> None:
    limits, grant = resolve(CONSERVATIVE)
    assert grant.leverage == 1.0 and limits.risk_per_trade == 0.005
    with pytest.raises(ProfileLocked):
        resolve(DEMO_10X_5PCT)
    digest = "a" * 64
    with pytest.raises(ProfileLocked):  # approved, but 10x needs a strategy validated at 10x
        resolve(DEMO_10X_5PCT, Approval("demo-10x-5pct", "owner", digest))
    with pytest.raises(ProfileLocked):  # an approval for another profile does not count
        resolve(DEMO_10X_5PCT, Approval("conservative", "owner", digest, True))
    limits, grant = resolve(DEMO_10X_5PCT, Approval("demo-10x-5pct", "owner", digest, True))
    assert grant.leverage == 10.0 and limits.risk_per_trade == 0.05


def test_three_way_splits_and_a_single_use_test() -> None:
    sp = three_way(1000, embargo=10)
    assert (sp.train, sp.validation, sp.test) == (slice(0, 590), slice(600, 790), slice(800, 1000))
    assert sp.open_test("final config") == slice(800, 1000)
    with pytest.raises(TestAlreadyUsed):
        sp.open_test("another variant")
    with pytest.raises(ValueError):
        three_way(20, embargo=10)


def test_eth_is_supported_too() -> None:
    s = minutes(500, seed=8, instrument=ETH_USD)
    st = EmaTrendScalp()
    st.prepare(s)
    assert st.explain(499, None)


def test_costs_count_into_the_stop_so_the_cap_holds() -> None:
    from jarvis.trading.risk import Account, EntryRequest

    limits, _ = resolve(CONSERVATIVE_SCALP)
    rm = RiskManager(limits)
    v = rm.check_entry(
        EntryRequest("a", BTC_USD, Side.LONG, 30_000.0, 29_970.0),
        Account(10_000, (), {"BTC-USD": 30_000.0}),
    )
    assert v.approved
    loss_at_stop_with_costs = v.qty * (30.0 + 30_000.0 * 0.0015)
    assert loss_at_stop_with_costs <= 10_000 * 0.005 + 1e-6  # 0.5 % including costs
    plain = RiskManager().check_entry(
        EntryRequest("b", BTC_USD, Side.LONG, 30_000.0, 29_970.0),
        Account(10_000, (), {"BTC-USD": 30_000.0}),
    )
    assert not plain.approved  # the swing profile's 0.2 % minimum stop still applies there
