"""Bar-level maker/taker execution: never a better fill than the bars allow."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from jarvis.trading.data import BarSeries, make_series
from jarvis.trading.instruments import BTC_USD
from jarvis.trading.scalping.execsim import (
    BASE,
    HARSH,
    OPTIMISTIC,
    Scenario,
    Setup,
    simulate,
    stats,
)
from jarvis.trading.scalping.setups_v2 import CANDIDATES, Params, context

M1 = 60_000
T0 = 1_767_225_600_000
FLAT = Scenario(
    "flat",
    maker_fee=0.0002,
    taker_fee=0.0005,
    spread_bps=0.0,
    taker_slip_bps=0.0,
    through_bps=1.0,
    partial_band_bps=0.0,
    adverse_bps=0.0,
)


def bars(rows: list[tuple[float, float, float, float]], vol: float = 1e6) -> BarSeries:
    return make_series(
        BTC_USD,
        M1,
        [(T0 + k * M1, o, h, lo, c, vol) for k, (o, h, lo, c) in enumerate(rows)],
        source="test",
        taker_buy=[vol / 2] * len(rows),
    )


def run(s: BarSeries, st: Setup, sc: Scenario = FLAT, **kw: Any) -> Any:
    return simulate(s, [st], sc, min_target_cost_mult=0.0, **kw)


def test_a_taker_fills_at_the_next_open_never_the_signal_close() -> None:
    s = bars(
        [(100, 100, 100, 100), (101, 103, 100.5, 102), (102, 110, 101, 109)] + [(109,) * 4] * 3
    )
    r = run(s, Setup(0, 1, "taker", stop=95.0, target=108.0), BASE)
    t = r.trades[0]
    assert t.entry == pytest.approx(101 * (1 + (0.25 + 1.5) / 10_000))  # next open + costs
    assert t.exit_kind == "target" and t.exit == 108.0


def test_a_touch_is_not_a_fill_trading_through_is() -> None:
    touch = bars(
        [(101, 101, 101, 101), (101, 101, 100.0, 100.5), (100.5, 101, 100.0, 100.8)]
        + [(101,) * 4] * 3
    )
    assert run(touch, Setup(0, 1, "maker", 99.0, 103.0, limit=100.0, ttl=2)).trades == []
    through = bars([(101, 101, 101, 101), (101, 101, 99.95, 100.5)] + [(101, 104, 100.5, 103)] * 3)
    t = run(through, Setup(0, 1, "maker", 99.0, 103.0, limit=100.0, ttl=2)).trades[0]
    assert t.entry == 100.0 and t.fill_frac == 1.0  # at the limit, never better
    assert t.fees == pytest.approx(t.qty * 100.0 * 0.0002 + t.qty * 103.0 * 0.0002)  # maker both


def test_partial_fills_and_expired_orders_are_counted() -> None:
    sc = Scenario(
        "p", spread_bps=0, taker_slip_bps=0, through_bps=1.0, partial_band_bps=4.0, adverse_bps=0.0
    )
    s = bars([(101, 101, 101, 101), (101, 101, 99.97, 100.5)] + [(101, 104, 100.5, 103)] * 3)
    r = run(s, Setup(0, 1, "maker", 99.0, 103.0, limit=100.0, ttl=2), sc)
    assert r.trades[0].fill_frac == pytest.approx(0.5) and r.orders["maker_partial"] == 1
    calm = bars([(101, 101, 101, 101)] * 6)
    r2 = run(calm, Setup(0, 1, "maker", 99.0, 103.0, limit=100.0, ttl=3), sc)
    assert r2.trades == [] and r2.orders["maker_unfilled"] == 1


def test_stop_wins_when_one_bar_touches_both_and_gaps_fill_at_the_open() -> None:
    both = bars([(100,) * 4, (100, 100, 100, 100), (100, 106, 94, 100)] + [(100,) * 4] * 3)
    t = run(both, Setup(0, 1, "taker", stop=95.0, target=105.0)).trades[0]
    assert t.exit_kind == "stop" and t.exit == 95.0
    gap = bars([(100,) * 4, (100, 100, 100, 100), (90, 91, 89, 90)] + [(90,) * 4] * 3)
    g = run(gap, Setup(0, 1, "taker", stop=95.0, target=105.0)).trades[0]
    assert g.exit_kind == "stop" and g.exit == 90.0  # the open after the gap, not the stop


def test_no_target_fill_on_the_fill_bar_and_time_stop_is_a_taker_exit() -> None:
    s = bars(
        [
            (101, 101, 101, 101),
            (101, 104, 99.9, 103.5),
            (103.5, 103.6, 102, 103),
            (103, 103.2, 102.5, 102.8),
            (102.8,) * 4,
            (102.8,) * 4,
        ]
    )
    t = run(s, Setup(0, 1, "maker", 95.0, 104.0, limit=100.0, ttl=2, max_hold=2)).trades[0]
    assert t.exit_kind == "time" and t.exit == 102.8  # open after the hold, taker


def test_risk_includes_costs_and_notional_is_capped() -> None:
    s = bars([(100,) * 4, (100, 100, 100, 100), (100, 100, 98, 98.5)] + [(98.5,) * 4] * 3)
    r = run(s, Setup(0, 1, "taker", stop=99.0, target=104.0), HARSH)
    t = r.trades[0]
    assert -t.net <= 10_000 * 0.005 * 1.05  # ~0.5 % incl. costs (gap slippage on top)
    big = run(s, Setup(0, 1, "taker", stop=99.99, target=104.0), BASE).trades[0]
    assert big.qty * big.entry <= 10_000 * 1.0001  # 1x cap


def test_setups_below_the_cost_multiple_are_skipped() -> None:
    s = bars([(100,) * 4] * 6)
    r = simulate(s, [Setup(0, 1, "taker", 99.9, 100.1)], BASE)  # 10 bp target
    assert r.trades == [] and r.skipped == {"target smaller than the cost multiple": 1}


def minutes(n: int, seed: int) -> BarSeries:
    rng = np.random.default_rng(seed)
    c = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    o = np.concatenate(([c[0]], c[:-1]))
    v = rng.uniform(5, 15, n) * np.where(rng.random(n) < 0.05, 6, 1)
    rows = [
        (T0 + k * M1, o[k], max(o[k], c[k]) * 1.0004, min(o[k], c[k]) * 0.9996, c[k], v[k])
        for k in range(n)
    ]
    return make_series(
        BTC_USD, M1, rows, source="test", taker_buy=list(v * rng.uniform(0.2, 0.8, n))
    )


@pytest.mark.parametrize("name", list(CANDIDATES))
@pytest.mark.parametrize("entry", ["maker", "taker"])
def test_setups_are_causal(name: str, entry: str) -> None:
    s = minutes(8 * 1440, seed=3)
    cut = 6 * 1440
    p = Params(entry=entry, mode="absorption" if name == "orderflow" else "continuation")
    full = [x for x in CANDIDATES[name](s, context(s), p) if x.i < cut]
    part = CANDIDATES[name](s.slice(0, cut), context(s.slice(0, cut)), p)
    assert full == part and len(full) > 0


def test_stats_report_uncertainty_and_adverse_selection() -> None:
    s = minutes(10 * 1440, seed=4)
    setups = CANDIDATES["breakout"](s, context(s), Params(entry="maker"))
    r = simulate(s, setups, OPTIMISTIC, min_target_cost_mult=0.0)
    st = stats(r)
    assert st["trades"] == len(r.trades) > 2
    assert st["avg_r_ci95"] is not None and 0 <= st["p_value"] <= 1
    assert set(st["adverse"]) == {"all_orders_bps", "filled_bps", "horizon_bars"}
    assert st["orders"]["maker_placed"] >= st["orders"]["maker_filled"]
