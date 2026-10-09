"""Simulated leverage: gated tiers, isolated margin, liquidation before nothing
else — and a risk per trade that leverage cannot change."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest

from jarvis.trading.data import HOUR_MS, BarSeries, make_series
from jarvis.trading.engine import run_backtest
from jarvis.trading.instruments import BTC_USD, ETH_USD
from jarvis.trading.leverage import (
    LeverageGrant,
    leverage_reasons,
    liquidation_price,
    stop_before_liquidation,
)
from jarvis.trading.paper import CostModel
from jarvis.trading.risk import Account, EntryRequest, OpenPosition, RiskLimits, RiskManager
from jarvis.trading.setups import StrategyBook
from jarvis.trading.strategies import Side, SmaCross, Target
from jarvis.trading.validation import EdgeCriteria, Verdict, compare_leverage, grid
from tests.fakes.fake_market import T0, trending

MARKS = {"BTC-USD": 100.0, "ETH-USD": 100.0}
WIDE = RiskLimits(max_gross_exposure=3.0)  # leverage only matters when exposure may exceed equity


def _req(
    lev: LeverageGrant,
    *,
    stop: float = 98.0,
    cid: str = "a",
    side: Side = Side.LONG,
    instrument=BTC_USD,
) -> EntryRequest:
    return EntryRequest(cid, instrument, side, 100.0, stop, None, lev)


# --- tiers --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("grant", "allowed"),
    [
        (LeverageGrant(1), True),
        (LeverageGrant(5), True),
        (LeverageGrant(6), False),  # above 5x needs validation at that leverage
        (LeverageGrant(10, validated=True), True),
        (LeverageGrant(15, validated=True), False),  # above 10x needs a review
        (LeverageGrant(20, validated=True, reviewed=True), True),
        (LeverageGrant(25, validated=True, reviewed=True), False),  # needs approval
        (LeverageGrant(30, validated=True, reviewed=True, experimental_approval=True), True),
        (LeverageGrant(31, validated=True, reviewed=True, experimental_approval=True), False),
        (LeverageGrant(0.5), False),
    ],
)
def test_leverage_tiers(grant: LeverageGrant, allowed: bool) -> None:
    assert (leverage_reasons(grant) == ()) is allowed


def test_targets_cannot_carry_leverage() -> None:
    """A strategy (or a model) cannot raise leverage per trade: it is not a field."""
    assert "leverage" not in {f.name for f in dataclasses.fields(Target)}


# --- liquidation math -------------------------------------------------------------------------


def test_isolated_liquidation_price_and_stop_buffer() -> None:
    long_liq = liquidation_price(Side.LONG, 100.0, 1.0, 10, mmr=0.005, fee_rate=0.0005)
    short_liq = liquidation_price(Side.SHORT, 100.0, 1.0, 10, mmr=0.005, fee_rate=0.0005)
    assert long_liq == pytest.approx(90.55) and short_liq == pytest.approx(109.45)
    assert stop_before_liquidation(Side.LONG, 100, 96, long_liq, buffer=0.5)
    assert not stop_before_liquidation(Side.LONG, 100, 95, long_liq, buffer=0.5)  # past half
    assert not stop_before_liquidation(Side.LONG, 100, 101, long_liq, buffer=0.5)
    assert stop_before_liquidation(Side.SHORT, 100, 104, short_liq, buffer=0.5)


# --- risk manager ----------------------------------------------------------------------------


def test_loss_at_the_stop_does_not_depend_on_leverage() -> None:
    a = RiskManager(WIDE).check_entry(_req(LeverageGrant(1), cid="1"), Account(10_000, (), MARKS))
    b = RiskManager(WIDE).check_entry(_req(LeverageGrant(5), cid="2"), Account(10_000, (), MARKS))
    assert a.approved and b.approved
    assert a.qty == b.qty and a.risk_amount == pytest.approx(b.risk_amount) == pytest.approx(50)
    assert b.margin == pytest.approx(a.margin / 5)
    assert b.liquidation is not None and b.liquidation < 98.0


def test_a_stop_too_close_to_liquidation_is_refused() -> None:
    grant = LeverageGrant(20, validated=True, reviewed=True)
    v = RiskManager(WIDE).check_entry(_req(grant, stop=96.0), Account(10_000, (), MARKS))
    assert "stop not safely before the liquidation price" in v.reasons
    tight = RiskManager(WIDE).check_entry(
        _req(grant, stop=99.0, cid="b"), Account(10_000, (), MARKS)
    )
    assert tight.approved


def test_unknown_maintenance_margin_blocks_the_trade() -> None:
    risky = dataclasses.replace(BTC_USD, mmr=0.15)
    v = RiskManager(WIDE).check_entry(
        _req(LeverageGrant(10, validated=True), instrument=risky), Account(10_000, (), MARKS)
    )
    assert any("maintenance margin" in r for r in v.reasons)


def test_margin_usage_is_capped() -> None:
    busy = [OpenPosition("X", Side.LONG, 1.0, 100.0, 99.0, 100.0, margin=4_900.0)]
    v = RiskManager(dataclasses.replace(WIDE, max_correlated_risk=0.05)).check_entry(
        _req(LeverageGrant(3)), Account(10_000, busy, MARKS, {frozenset(("X", "BTC-USD")): 0.0})
    )
    assert "margin usage would exceed the limit" in v.reasons


def test_correlated_same_direction_risk_is_capped_and_unknown_counts_as_correlated() -> None:
    eth = OpenPosition("ETH-USD", Side.LONG, 30.0, 100.0, 98.0, 100.0)  # 60 at risk
    unknown = RiskManager().check_entry(_req(LeverageGrant()), Account(10_000, [eth], MARKS))
    assert "correlated risk in this direction would exceed the limit" in unknown.reasons
    low = Account(10_000, [eth], MARKS, {frozenset(("ETH-USD", "BTC-USD")): 0.2})
    assert RiskManager().check_entry(_req(LeverageGrant(), cid="b"), low).approved
    hedge = RiskManager().check_entry(
        _req(LeverageGrant(), cid="c", side=Side.SHORT, stop=102.0), Account(10_000, [eth], MARKS)
    )
    assert hedge.approved  # the opposite direction does not add to it


# --- engine ------------------------------------------------------------------------------------


class OneLong:
    name = "one_long"
    params: Mapping[str, Any] = {}

    def __init__(self, stop: float) -> None:
        self.stop = stop

    def prepare(self, series: BarSeries) -> None:
        return None

    def decide(self, i: int, current: Side | None) -> Target | None:
        return Target(Side.LONG, self.stop, None, "test") if i == 0 and current is None else None


def _series(bars: list[tuple[float, float, float, float]]) -> BarSeries:
    rows = [(T0 + k * HOUR_MS, o, h, lo, c, 10.0) for k, (o, h, lo, c) in enumerate(bars)]
    return make_series(BTC_USD, HOUR_MS, rows, source="test")


FLAT = (100.0, 100.4, 99.6, 100.0)
COSTS = CostModel(fee_rate=0.0005, slippage_bps=0.0, funding_rate_8h=0.0)


def test_a_gap_through_the_liquidation_loses_exactly_the_isolated_margin() -> None:
    series = _series([FLAT, FLAT, (85.0, 86.0, 84.0, 85.0), FLAT])
    grant = {"one_long": LeverageGrant(10, validated=True)}
    (trade,) = run_backtest(series, OneLong(96.0), costs=COSTS, limits=WIDE, leverage=grant).trades
    margin, entry_fee = 12.5 * 100 / 10, 12.5 * 100 * 0.0005
    assert trade.exit_reason == "liquidation" and trade.exit == pytest.approx(90.55)
    assert trade.net == pytest.approx(-(margin + entry_fee))
    (spot,) = run_backtest(series, OneLong(96.0), costs=COSTS, limits=WIDE).trades
    assert spot.exit_reason == "stop" and spot.exit == pytest.approx(85.0)  # 1x: no liquidation


def test_without_a_gap_the_stop_fires_before_liquidation() -> None:
    series = _series([FLAT, FLAT, (99.0, 99.5, 80.0, 82.0), FLAT])
    grant = {"one_long": LeverageGrant(10, validated=True)}
    (trade,) = run_backtest(series, OneLong(96.0), costs=COSTS, limits=WIDE, leverage=grant).trades
    assert trade.exit_reason == "stop" and trade.exit == pytest.approx(96.0)


def test_an_unvalidated_high_leverage_is_refused_in_the_engine() -> None:
    series = _series([FLAT] * 4)
    grant = {"one_long": LeverageGrant(8)}
    assert run_backtest(series, OneLong(99.0), limits=WIDE, leverage=grant).trades == ()


# --- validation and the book -------------------------------------------------------------


def test_each_leverage_is_validated_separately_against_the_unlevered_baseline() -> None:
    result = compare_leverage(
        trending(4000),
        "sma",
        SmaCross,
        grid(fast=[10, 20], slow=[50]),
        levels=(3,),
        criteria=EdgeCriteria(bootstrap=300, min_trades=10),
        limits=WIDE,
    )
    assert [v.family for v in result.verdicts] == ["sma@1x", "sma@3x"]
    assert all(v.alpha_used == pytest.approx(0.05 / 2) for v in result.verdicts)
    with pytest.raises(ValueError):
        compare_leverage(trending(1000), "sma", SmaCross, grid(fast=[10], slow=[50]), levels=(25,))
    with pytest.raises(ValueError):
        compare_leverage(
            trending(1000),
            "sma",
            SmaCross,
            grid(fast=[10], slow=[50]),
            levels=(35,),
            experimental_approval=True,
        )


def test_the_book_hands_the_engine_a_fixed_leverage() -> None:
    book = StrategyBook()
    verdict = Verdict("sma", True, ("t",), 40, 1.5, 0.3, 0.001, 0.05, ())
    book.add(SmaCross(name="sma@3x"), verdict, leverage=LeverageGrant(3))
    book.add(SmaCross(name="sma@1x"), verdict)
    assert book.grants() == {"sma@3x": LeverageGrant(3), "sma@1x": LeverageGrant(1)}
    with pytest.raises(ValueError):
        book.add(SmaCross(name="sma@1x"), verdict)  # one name, one record


def test_eth_is_covered_too() -> None:
    v = RiskManager(WIDE).check_entry(
        _req(LeverageGrant(2), instrument=ETH_USD, side=Side.SHORT, stop=102.0),
        Account(10_000, (), MARKS),
    )
    assert v.approved and v.liquidation is not None and v.liquidation > 102.0
