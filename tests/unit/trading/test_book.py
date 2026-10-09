"""Several strategies, one account: only validated strategies trade, setups are
ranked by objective numbers, no strategy needs an external signal, and every
trade stays attributed to the strategy that made it."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Any

import numpy as np

from jarvis.trading.data import HOUR_MS, BarSeries, make_series
from jarvis.trading.engine import run_backtest
from jarvis.trading.instruments import BTC_USD, ETH_USD
from jarvis.trading.journal import MemoryJournal
from jarvis.trading.setups import StrategyBook, symbol_matches
from jarvis.trading.signal_strategy import SignalStrategy, interval_minutes
from jarvis.trading.signals import ExternalSignal, SignalLog
from jarvis.trading.strategies import Side, Target
from jarvis.trading.validation import EdgeCriteria, Verdict, compare, grid
from tests.fakes.fake_market import T0, random_walk


def _verdict(name: str, tradable: bool, edge: float = 0.3, p: float = 0.001) -> Verdict:
    return Verdict(name, tradable, ("test",), 50, 1.5, edge, p, 0.05, ())


class Every:
    """Proposes a long with a 1 % stop every ``k`` bars; exits after 3 bars."""

    def __init__(self, name: str, k: int = 10, offset: int = 0) -> None:
        self.name, self.k, self.offset = name, k, offset
        self.params: Mapping[str, Any] = {"k": k}
        self.proposed = 0

    def prepare(self, series: BarSeries) -> None:
        self._close = series.close

    def decide(self, i: int, current: Side | None) -> Target | None:
        c = float(self._close[i])
        if current is None and i % self.k == self.offset and i > 20:
            self.proposed += 1
            return Target(Side.LONG, c * 0.99, c * 1.03, f"{self.name} rule")
        if current is not None and i % self.k == (self.offset + 3) % self.k:
            return Target(None, reason="time exit")
        return None


def test_only_validated_strategies_trade() -> None:
    series = random_walk(400, seed=1)
    book = StrategyBook()
    good, unproven = Every("good", 10, 0), Every("unproven", 10, 5)
    book.add(good, _verdict("good", True))
    book.add(unproven, _verdict("unproven", False))
    result = run_backtest(series, [good, unproven], rank=book.rank)
    assert unproven.proposed > 0
    assert {t.strategy for t in result.trades} == {"good"}
    assert book.active() == [good]


def test_no_strategy_needs_an_external_signal() -> None:
    series = random_walk(400, seed=2)
    book = StrategyBook(confirmations=SignalLog())  # an empty signal log
    rule = Every("rule_only", 10, 0)
    book.add(rule, _verdict("rule_only", True))
    result = run_backtest(series, [rule], rank=book.rank)
    assert result.trades and all(t.strategy == "rule_only" for t in result.trades)


def test_the_better_validated_setup_gets_the_instrument_first() -> None:
    series = random_walk(400, seed=3)
    weaker, stronger = Every("weaker", 10, 0), Every("stronger", 10, 0)  # same bars
    book = StrategyBook()
    book.add(weaker, _verdict("weaker", True, edge=0.2))
    book.add(stronger, _verdict("stronger", True, edge=0.6))
    journal = MemoryJournal()
    result = run_backtest(series, [weaker, stronger], rank=book.rank, journal=journal)
    assert {t.strategy for t in result.trades} == {"stronger"}
    first = journal.kinds("decision")[0]
    assert first["strategy"] == "stronger"


def test_trades_are_attributed_per_strategy() -> None:
    series = random_walk(600, seed=4)
    a, b = Every("a", 20, 0), Every("b", 20, 10)
    book = StrategyBook()
    book.add(a, _verdict("a", True))
    book.add(b, _verdict("b", True))
    result = run_backtest(series, [a, b], rank=book.rank)
    per = result.metrics["by_strategy"]
    assert set(per) == {"a", "b"}
    assert sum(p["trades"] for p in per.values()) == len(result.trades)
    assert np.isclose(sum(p["net_pnl"] for p in per.values()), sum(t.net for t in result.trades))


def _sig(
    series: BarSeries,
    k: int,
    direction: str,
    *,
    delay_ms: int = 0,
    interval: str = "60",
    symbol: str = "BTCUSDT.P",
) -> ExternalSignal:
    bar = int(series.ts[k])
    return ExternalSignal(
        "tradingview",
        "PVSRA",
        "climax",
        direction,
        symbol,
        "X",
        interval,
        bar,
        bar + HOUR_MS,
        bar + HOUR_MS + delay_ms,
        None,
    )


def test_a_late_signal_is_used_at_the_first_close_after_it_arrived() -> None:
    series = random_walk(200, seed=5)
    on_time = SignalStrategy([_sig(series, 50, "long", delay_ms=0)], name="tv")
    late = SignalStrategy([_sig(series, 50, "long", delay_ms=30 * 60_000)], name="tv_late")
    for s in (on_time, late):
        s.prepare(series)
    assert on_time.decide(50, None) is not None  # fired at the close of bar 50
    assert late.decide(50, None) is None and late.decide(51, None) is not None


def test_conflicts_other_timeframes_and_other_coins_are_ignored() -> None:
    series = random_walk(200, seed=6)
    s = SignalStrategy(
        [
            _sig(series, 40, "long"),
            _sig(series, 40, "short"),  # contradiction
            _sig(series, 60, "long", interval="240"),  # 4h signal on a 1h series
            _sig(series, 80, "long", symbol="ETHUSDT"),
            _sig(series, 100, "short"),
        ],
        name="tv",
        hold_bars=5,
    )
    s.prepare(series)
    assert [i for i in range(200) if s.decide(i, None)] == [100]
    assert s.decide(104, Side.SHORT) is None and s.decide(105, Side.SHORT).side is None


def test_a_validated_signal_strategy_trades_beside_rule_strategies() -> None:
    series = random_walk(800, seed=7)
    rng = np.random.default_rng(3)
    signals = [
        _sig(series, int(k), "long" if rng.random() > 0.5 else "short")
        for k in rng.choice(np.arange(30, 780), 25, replace=False)
    ]
    tv, rule = SignalStrategy(signals, name="tv_pvsra"), Every("rule", 15, 0)
    book = StrategyBook()
    book.add(tv, _verdict("tv_pvsra", True, edge=0.5))
    book.add(rule, _verdict("rule", True, edge=0.1))
    result = run_backtest(series, [tv, rule], rank=book.rank)
    assert {"tv_pvsra", "rule"} <= {t.strategy for t in result.trades}


def test_signal_strategies_go_through_the_same_validation() -> None:
    series = random_walk(3000, seed=8)
    rng = np.random.default_rng(4)
    signals = [
        _sig(series, int(k), "long" if rng.random() > 0.5 else "short")
        for k in rng.choice(np.arange(30, 2980), 150, replace=False)
    ]
    factory = partial(SignalStrategy, signals, name="tv_random")
    result = compare(
        series,
        {"tv_random": (factory, grid(stop_atr=[1.5, 2.5]))},
        criteria=EdgeCriteria(bootstrap=500),
    )
    assert result.decision == "no_trade"  # random alerts earn no permission to trade


def test_confirmation_is_recorded_but_never_required() -> None:
    series = random_walk(100, seed=9)
    log = SignalLog()
    log.add(_sig(series, 49, "long"))
    rule = Every("rule", 50, 0)
    rule.prepare(series)
    book = StrategyBook(confirmations=log)
    book.add(rule, _verdict("rule", True))
    candidates = [(rule, rule.decide(50, None))]
    (with_sig,) = book.setups(series, 50, candidates)
    assert with_sig.confirmed_by == ("PVSRA:climax",)
    plain = StrategyBook()
    plain.add(Every("rule", 50, 0), _verdict("rule", True))
    (without,) = plain.setups(series, 50, candidates)
    assert without.confirmed_by == () and without.edge_r == with_sig.edge_r


def test_symbols_and_intervals() -> None:
    assert symbol_matches("BTCUSDT.P", BTC_USD) and symbol_matches("XBTUSD", BTC_USD)
    assert not symbol_matches("BTCDOMUSDT", BTC_USD) and symbol_matches("ETH-USD", ETH_USD)
    assert interval_minutes("60") == 60 and interval_minutes("1D") == 1440
    assert interval_minutes("D") == 1440 and interval_minutes("4H".replace("H", "")) == 4


def test_series_helper() -> None:
    rows = [(T0 + k * HOUR_MS, 1.0, 1.0, 1.0, 1.0, 1.0) for k in range(3)]
    assert len(make_series(BTC_USD, HOUR_MS, rows, source="t")) == 3
