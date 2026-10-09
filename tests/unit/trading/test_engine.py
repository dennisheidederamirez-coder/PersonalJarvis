"""The bar loop: decisions at the close, fills at the next open, every cost
charged, stops before targets, and no decision that could see the future."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from jarvis.trading.data import HOUR_MS, BarSeries, DataQualityError, make_series
from jarvis.trading.engine import DemoTrader, run_backtest
from jarvis.trading.instruments import BTC_USD
from jarvis.trading.journal import MemoryJournal
from jarvis.trading.paper import CostModel, PaperBroker
from jarvis.trading.risk import RiskManager
from jarvis.trading.strategies import DonchianBreakout, RsiReversion, Side, SmaCross, Target
from tests.fakes.fake_market import T0, random_walk

COSTS = CostModel(fee_rate=0.001, slippage_bps=10.0, funding_rate_8h=0.0)


def _series(bars: list[tuple[float, float, float, float]]) -> BarSeries:
    rows = [(T0 + k * HOUR_MS, o, h, lo, c, 100.0) for k, (o, h, lo, c) in enumerate(bars)]
    return make_series(BTC_USD, HOUR_MS, rows, source="test", retrieved_at="2026-10-09")


class Script:
    """Targets by bar index; nothing else."""

    name = "script"
    params: Mapping[str, Any] = {}

    def __init__(self, targets: dict[int, Target]) -> None:
        self.targets = targets
        self.seen: list[tuple[int, Side | None]] = []

    def prepare(self, series: BarSeries) -> None:
        return None

    def decide(self, i: int, current: Side | None) -> Target | None:
        self.seen.append((i, current))
        return self.targets.get(i)


FLAT_BAR = (100.0, 100.5, 99.5, 100.0)


def test_entry_at_next_open_and_every_cost_is_charged() -> None:
    series = _series([FLAT_BAR, FLAT_BAR, (100, 111, 99.5, 110), (110, 110.5, 109.5, 110)])
    script = Script({0: Target(Side.LONG, 90.0, 130.0, "go"), 2: Target(None, reason="done")})
    result = run_backtest(series, script, costs=COSTS)
    (trade,) = result.trades
    qty = 5.0  # 0.5 % of 10 000 = 50 at risk / 10 stop distance
    entry, exit_ = 100 * 1.001, 110 * 0.999  # 10 bps slippage each way
    fees = entry * qty * 0.001 + exit_ * qty * 0.001
    assert trade.qty == pytest.approx(qty)
    assert (trade.entry_ms, trade.exit_ms) == (T0 + HOUR_MS, T0 + 3 * HOUR_MS)  # next opens
    assert trade.entry == pytest.approx(entry) and trade.exit == pytest.approx(exit_)
    assert trade.fees == pytest.approx(fees)
    assert trade.net == pytest.approx((exit_ - entry) * qty - fees)
    assert result.equity[-1] == pytest.approx(10_000 + trade.net)
    assert trade.slippage == pytest.approx((0.1 + 0.11) * qty)


def test_stop_beats_target_inside_one_bar_and_gaps_fill_at_the_open() -> None:
    both = _series([FLAT_BAR, FLAT_BAR, (100, 106, 94, 100), FLAT_BAR])
    (t,) = run_backtest(both, Script({0: Target(Side.LONG, 95.0, 105.0)}), costs=COSTS).trades
    assert t.exit_reason == "stop" and t.exit == pytest.approx(95 * 0.999)

    gap = _series([FLAT_BAR, FLAT_BAR, (93, 94, 92, 93), FLAT_BAR])
    (g,) = run_backtest(gap, Script({0: Target(Side.LONG, 95.0, 105.0)}), costs=COSTS).trades
    assert g.exit == pytest.approx(93 * 0.999)  # worse than the stop: the market gapped

    short = _series([FLAT_BAR, FLAT_BAR, (100, 100.5, 89, 90), FLAT_BAR])
    (s,) = run_backtest(short, Script({0: Target(Side.SHORT, 105.0, 90.0)}), costs=COSTS).trades
    assert s.exit_reason == "take_profit" and s.net > 0


def test_an_entry_the_market_gapped_through_is_cancelled() -> None:
    series = _series([FLAT_BAR, (98, 98.5, 97.5, 98), FLAT_BAR])
    journal = MemoryJournal()
    result = run_backtest(series, Script({0: Target(Side.LONG, 99.0, 103.0)}), journal=journal)
    assert result.trades == ()
    assert journal.kinds("cancelled")[0]["reason"] == "gapped through the stop"


def test_funding_costs_longs_and_pays_shorts() -> None:
    broker = PaperBroker(10_000, CostModel(0.0, 0.0, 0.0008))
    broker.open("a", BTC_USD, Side.LONG, 1.0, 100.0, 90.0, None, T0)
    assert broker.accrue_funding("BTC-USD", 100.0, 8.0) == pytest.approx(0.08)
    assert broker.cash == pytest.approx(10_000 - 0.08)
    broker.close("BTC-USD", 100.0, T0, "x", "b")
    broker.open("c", BTC_USD, Side.SHORT, 1.0, 100.0, 110.0, None, T0)
    assert broker.accrue_funding("BTC-USD", 100.0, 8.0) == pytest.approx(-0.08)


def test_a_reversal_closes_then_opens_in_one_step() -> None:
    series = _series([FLAT_BAR] * 4)
    script = Script({0: Target(Side.LONG, 95.0, 120.0), 1: Target(Side.SHORT, 105.0, 80.0)})
    result = run_backtest(series, script)
    assert [t.side for t in result.trades] == [Side.LONG, Side.SHORT]
    assert result.trades[0].exit_ms == result.trades[1].entry_ms == T0 + 2 * HOUR_MS


@pytest.mark.parametrize(
    "make", [lambda: SmaCross(10, 30), lambda: DonchianBreakout(20, 10), lambda: RsiReversion()]
)
def test_decisions_never_depend_on_future_bars(make: Any) -> None:
    series = random_walk(800, seed=5)
    full, cut = make(), make()
    full.prepare(series)
    cut.prepare(series.slice(0, 500))
    for i in range(500):
        for current in (None, Side.LONG, Side.SHORT):
            assert full.decide(i, current) == cut.decide(i, current)


def test_the_same_loop_runs_bar_by_bar_as_a_live_demo() -> None:
    series = random_walk(600, seed=8)
    strategy = SmaCross(10, 30)
    strategy.prepare(series)
    trader = DemoTrader(PaperBroker(10_000), RiskManager())
    for i in range(len(series)):
        trader.process_bar(series, i, strategy)
    replay = run_backtest(series, SmaCross(10, 30))
    assert [t.client_id for t in trader.trades] == [t.client_id for t in replay.trades][
        : len(trader.trades)
    ]


def test_broken_data_is_refused() -> None:
    series = _series([FLAT_BAR, (100, 99, 101, 100), FLAT_BAR])  # high below low
    with pytest.raises(DataQualityError):
        run_backtest(series, Script({}))


def test_a_trading_window_uses_earlier_bars_only_as_history() -> None:
    series = random_walk(1000, seed=4)
    result = run_backtest(series, SmaCross(10, 30), start=600)
    assert all(t.entry_ms > int(series.ts[600]) for t in result.trades)
    assert len(result.equity) == 1 + 400
    assert np.isfinite(result.metrics["sharpe"])
