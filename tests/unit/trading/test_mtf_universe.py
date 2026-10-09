"""Multi-timeframe without look-ahead, a universe without survivors' bias, and
a final holdout no choice ever saw."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from jarvis.trading.availability import Listing, coverage, eligible, feature_span, listings
from jarvis.trading.data import DAY_MS, HOUR_MS, make_series
from jarvis.trading.instruments import (
    BTC_USD,
    GOLD_FUTURES,
    HBAR_USD,
    HYPE_USD,
    PAXG_USD,
    PRIORITY_ALTCOINS,
    RESEARCH_UNIVERSE,
    XAU_SPOT,
    AssetClass,
)
from jarvis.trading.strategies import DonchianBreakout, SmaCross
from jarvis.trading.timeframes import H1, H4, M15, HigherTimeframeFilter, align, resample
from jarvis.trading.validation import EdgeCriteria, compare, edge_verdict, grid, walk_forward
from tests.fakes.fake_market import T0, random_walk, trending

# --- timeframes ---------------------------------------------------------------------------


def test_resample_builds_complete_bars_and_drops_incomplete_ones() -> None:
    s = random_walk(4 * 10, seed=1)  # 40 hourly bars from 00:00
    h4 = resample(s, H4)
    assert len(h4) == 10 and h4.interval_ms == H4
    assert h4.open[0] == s.open[0] and h4.close[0] == s.close[3]
    assert h4.high[0] == pytest.approx(s.high[:4].max()) and h4.volume[0] == pytest.approx(
        s.volume[:4].sum()
    )
    gappy = s.slice(0, 5)  # bucket 2 only half there
    keep = np.concatenate([np.arange(0, 4), np.arange(6, 8)])
    holed = dataclasses.replace(
        gappy,
        ts=s.ts[keep],
        open=s.open[keep],
        high=s.high[keep],
        low=s.low[keep],
        close=s.close[keep],
        volume=s.volume[keep],
    )
    assert len(resample(holed, H4)) == 1  # the gappy bucket is dropped, not invented
    assert resample(s, H4).source.endswith("@4h")


def test_a_higher_bar_is_only_visible_after_it_closed() -> None:
    s = random_walk(48, seed=2)
    h4 = resample(s, H4)
    idx = align(s, h4)
    assert list(idx[:4]) == [-1, -1, -1, 0]  # the first 4h bar closes with the 4th hour
    assert list(idx[4:8]) == [0, 0, 0, 1]
    for i, j in enumerate(idx):
        if j >= 0:
            assert h4.ts[j] + H4 <= s.ts[i] + s.interval_ms


def test_the_trend_filter_never_uses_future_bars() -> None:
    s = random_walk(1500, seed=3)
    full = HigherTimeframeFilter(SmaCross(10, 30), H4, fast=5, slow=10)
    cut = HigherTimeframeFilter(SmaCross(10, 30), H4, fast=5, slow=10)
    full.prepare(s)
    cut.prepare(s.slice(0, 900))
    for i in range(900):
        for cur in (None,):
            assert full.decide(i, cur) == cut.decide(i, cur)
    assert full.name == "sma_cross+trend4h"


def test_mtf_confirmation_is_its_own_family_that_must_prove_itself() -> None:
    s = random_walk(3000, seed=4)
    result = compare(
        s,
        {
            "plain": (SmaCross, grid(fast=[10], slow=[30])),
            "filtered": (
                lambda **p: HigherTimeframeFilter(SmaCross(**p), H4, fast=5, slow=10),
                grid(fast=[10], slow=[30]),
            ),
        },
        criteria=EdgeCriteria(bootstrap=300),
    )
    assert [v.family for v in result.verdicts] == ["plain", "filtered"]
    assert result.decision == "no_trade"  # noise stays noise, filtered or not


def test_15_minute_bars_resample_to_hours() -> None:
    rows = [(T0 + k * M15, 100.0, 101.0, 99.0, 100.0 + k % 4, 1.0) for k in range(16)]
    s = make_series(BTC_USD, M15, rows, source="bybit:perp:BTCUSDT")
    h1 = resample(s, H1)
    assert len(h1) == 4 and h1.close[0] == 103.0 and h1.volume[0] == 4.0


# --- universe -----------------------------------------------------------------------------


def test_priority_altcoins_are_in_the_research_universe_and_not_yet_tradable() -> None:
    assert PRIORITY_ALTCOINS == (HYPE_USD, HBAR_USD)
    assert {i.base for i in RESEARCH_UNIVERSE} >= {"SOL", "XRP", "BNB", "LINK", "HYPE", "HBAR"}
    assert all(not i.demo_tradable for i in RESEARCH_UNIVERSE)


def test_gold_forms_are_distinct_instruments() -> None:
    forms = {XAU_SPOT.symbol, GOLD_FUTURES.symbol, PAXG_USD.symbol}
    assert len(forms) == 3
    assert (
        XAU_SPOT.asset_class is AssetClass.COMMODITY and PAXG_USD.asset_class is AssetClass.CRYPTO
    )
    assert XAU_SPOT.session != PAXG_USD.session
    assert not XAU_SPOT.demo_tradable


def test_an_instrument_enters_a_test_only_after_its_listing_plus_warmup() -> None:
    btc = random_walk(24 * 400, seed=5)
    hype_rows = [
        (T0 + 300 * DAY_MS + k * HOUR_MS, 10.0, 10.1, 9.9, 10.0, 1.0) for k in range(24 * 100)
    ]
    hype = make_series(HYPE_USD, HOUR_MS, hype_rows, source="bybit:perp:HYPEUSDT")
    lst = listings([coverage(btc), coverage(hype)])
    assert lst["HYPE-USD"].first_ms == T0 + 300 * DAY_MS
    universe = [*lst.values(), Listing("OLD-USD", T0, last_ms=T0 + 100 * DAY_MS)]
    at_200 = eligible(universe, T0 + 200 * DAY_MS, min_history_ms=60 * DAY_MS)
    at_380 = eligible(universe, T0 + 380 * DAY_MS, min_history_ms=60 * DAY_MS)
    assert at_200 == ["BTC-USD"]  # HYPE not listed yet; the delisted coin is gone
    assert at_380 == ["BTC-USD", "HYPE-USD"]
    assert eligible(universe, T0 + 90 * DAY_MS, min_history_ms=30 * DAY_MS) == [
        "BTC-USD",
        "OLD-USD",
    ]  # still traded then: kept, no survivorship bias


def test_features_exist_only_where_the_source_covered_them() -> None:
    s = random_walk(100, seed=6)
    oi_ts = [int(t) for t in s.ts[40:70]]
    assert feature_span(s, oi_ts) == (40, 70)
    assert feature_span(s, []) is None


# --- final holdout ------------------------------------------------------------------------


def test_the_final_holdout_is_untouched_and_must_confirm() -> None:
    trend = trending(5000)
    v = walk_forward(
        trend,
        "donchian",
        DonchianBreakout,
        grid(entry_n=[20, 55], exit_n=[10]),
        criteria=EdgeCriteria(bootstrap=300),
        holdout_frac=0.2,
    )
    assert v.holdout is not None and v.holdout["trades"] > 0
    assert all(f.get("fold") for f in v.fold_results)
    assert v.fold_consistency >= 0.5 and v.tradable

    # the same edge in the folds, but the final period is pure noise
    noise = random_walk(1250, seed=7)
    scale = trend.close[3749] / noise.close[0]
    tail = dataclasses.replace(
        noise,
        ts=trend.ts[3750:5000],
        open=noise.open * scale,
        high=noise.high * scale,
        low=noise.low * scale,
        close=noise.close * scale,
    )
    joined = dataclasses.replace(
        trend,
        open=np.concatenate([trend.open[:3750], tail.open]),
        high=np.concatenate([trend.high[:3750], tail.high]),
        low=np.concatenate([trend.low[:3750], tail.low]),
        close=np.concatenate([trend.close[:3750], tail.close]),
        volume=np.concatenate([trend.volume[:3750], tail.volume]),
    )
    late = walk_forward(
        joined,
        "donchian",
        DonchianBreakout,
        grid(entry_n=[20, 55], exit_n=[10]),
        criteria=EdgeCriteria(bootstrap=300),
        holdout_frac=0.25,
    )
    assert late.holdout is not None
    if not late.tradable:
        assert any("holdout" in r or "unstable" in r or "significant" in r for r in late.reasons)


def test_more_combinations_tested_means_a_stricter_bar() -> None:
    v = walk_forward(
        trending(3000),
        "sma",
        SmaCross,
        grid(fast=[10], slow=[50]),
        criteria=EdgeCriteria(bootstrap=200),
        extra_tests=12,
    )
    assert v.alpha_used == pytest.approx(0.05 / 12)


def test_a_holdout_that_does_not_confirm_blocks_the_strategy() -> None:
    crit = EdgeCriteria(min_trades=0, min_profit_factor=0.0, alpha=1.0)
    folds = [{"fold": 1, "trades": 5, "return_pct": 0.02}]
    bad = edge_verdict(
        "x",
        [],
        [],
        folds,
        n_families=1,
        criteria=crit,
        holdout={"trades": 6, "avg_r": -0.3, "profit_factor": 0.6},
    )
    none = edge_verdict("x", [], [], folds, n_families=1, criteria=crit, holdout={"trades": 0})
    assert "the final holdout period does not confirm the edge" in bad.reasons
    assert "no trade in the final holdout period" in none.reasons
    shaky = edge_verdict(
        "x",
        [],
        [],
        [{"fold": k, "trades": 3, "return_pct": r} for k, r in enumerate([0.1, -0.1, -0.2, -0.05])],
        n_families=1,
        criteria=crit,
    )
    assert any("unstable" in r for r in shaky.reasons) and shaky.fold_consistency == 0.25


def test_research_instruments_can_be_backtested_but_not_demo_traded() -> None:
    from jarvis.trading.engine import run_backtest
    from jarvis.trading.instruments import SOL_USD
    from jarvis.trading.risk import Account, EntryRequest, RiskManager
    from jarvis.trading.strategies import Side

    sol = trending(3000, instrument=SOL_USD)
    assert run_backtest(sol, SmaCross(10, 30)).trades  # history may be simulated
    live = RiskManager()  # what a demo account uses
    v = live.check_entry(
        EntryRequest("a", SOL_USD, Side.LONG, 100.0, 98.0), Account(10_000, (), {"SOL-USD": 100.0})
    )
    assert not v.approved and any("analysis-only" in r for r in v.reasons)


def test_scalping_timeframes_resample_without_inventing_bars() -> None:
    from jarvis.trading.timeframes import M1, M3, M5

    rows = [(T0 + k * M1, 100.0, 101.0, 99.0, 100.0 + k % 5, 1.0) for k in range(30)]
    m1 = make_series(BTC_USD, M1, rows, source="binance:perp:BTCUSDT")
    m3, m5 = resample(m1, M3), resample(m1, M5)
    assert (len(m3), len(m5)) == (10, 6)
    assert m3.close[0] == m1.close[2] and m5.volume[0] == 5.0
    assert list(align(m1, m5)[:6]) == [-1, -1, -1, -1, 0, 0]  # visible only once closed
