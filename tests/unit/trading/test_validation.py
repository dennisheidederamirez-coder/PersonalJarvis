"""No supported edge, no trade — and a real edge is recognised out of sample."""

from __future__ import annotations

import pytest

from jarvis.trading.strategies import DonchianBreakout, RsiReversion, SmaCross
from jarvis.trading.validation import EdgeCriteria, bootstrap_p_value, compare, grid
from tests.fakes.fake_market import mean_reverting, random_walk, trending

FAMILIES = {
    "sma_cross": (SmaCross, grid(fast=[10, 20], slow=[50, 100])),
    "donchian": (DonchianBreakout, grid(entry_n=[20, 55], exit_n=[10, 20])),
    "rsi": (RsiReversion, grid(low=[25, 30], high=[70, 75])),
}
FAST = EdgeCriteria(bootstrap=1000)


@pytest.mark.parametrize("seed", [1, 11, 21])
def test_a_random_walk_never_gets_a_trade(seed: int) -> None:
    result = compare(random_walk(3000, seed=seed), FAMILIES, criteria=FAST)
    assert result.decision == "no_trade"
    assert result.best is None
    assert all(not v.tradable and v.reasons for v in result.verdicts)


def test_a_real_trend_is_recognised_and_reversion_is_not_chosen_there() -> None:
    result = compare(trending(5000), FAMILIES, criteria=FAST)
    assert result.decision == "trade"
    assert result.best is not None and result.best.family in {"donchian", "sma_cross"}
    rsi = next(v for v in result.verdicts if v.family == "rsi")
    assert not rsi.tradable
    assert result.best.chosen_params  # the in-sample choice per fold is documented
    assert all("fold" in f for f in result.best.fold_results)
    # Bonferroni: comparing three families tightens the bar for each of them
    assert all(v.alpha_used == pytest.approx(0.05 / 3) for v in result.verdicts)


def test_too_few_trades_are_not_an_edge_even_when_profitable() -> None:
    result = compare(mean_reverting(3000), {"rsi": FAMILIES["rsi"]}, criteria=FAST)
    (v,) = result.verdicts
    assert v.oos_profit_factor > 1.2 and v.oos_trades < FAST.min_trades
    assert not v.tradable and "out-of-sample trades" in v.reasons[0]


def test_bootstrap_p_value() -> None:
    assert bootstrap_p_value([-1.0, 0.5, -0.2], n=500, seed=1) == 1.0
    strong = [1.0, 1.2, 0.8, 1.1, 0.9] * 10
    assert bootstrap_p_value(strong, n=500, seed=1) < 0.01
    noisy = [1.0, -1.0, 0.9, -1.1, 1.05, -0.95] * 5
    assert bootstrap_p_value(noisy, n=2000, seed=1) > 0.05


def test_grid_is_the_cartesian_product() -> None:
    assert grid(a=[1, 2], b=["x"]) == [{"a": 1, "b": "x"}, {"a": 2, "b": "x"}]
