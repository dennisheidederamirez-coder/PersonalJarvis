"""One scalping configuration, one period: the building block of a study.

A configuration is (strategy with its library defaults, symbol, timeframe,
round-trip cost level). It runs on ONE period of the pre-registered split
(train or validation; the test is opened elsewhere and only once), with the
conservative scalping profile and the portfolio controller in front of it.
Bars before the period serve as indicator warm-up only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import numpy as np

from jarvis.trading.data import BarSeries
from jarvis.trading.engine import run_backtest
from jarvis.trading.paper import CostModel
from jarvis.trading.scalping.controller import ControllerLimits, PortfolioController
from jarvis.trading.scalping.profiles import CONSERVATIVE_SCALP, resolve
from jarvis.trading.scalping.strategies import EmaTrendScalp, OrderflowLevelScalp, ScalpBreakout
from jarvis.trading.validation import bootstrap_p_value

TAKER_FEE = 0.0005
SPREAD_BPS = 2.0
STRATEGIES: Mapping[str, Any] = {
    "scalp_breakout": ScalpBreakout,
    "ema_trend_scalp": EmaTrendScalp,
    "orderflow_level_scalp": OrderflowLevelScalp,
}


def cost_model(round_trip: float, funding_rate_8h: float = 0.0001) -> CostModel:
    """Split a round-trip cost into two taker fees, the spread (paid half per
    fill) and the rest as slippage per fill."""
    slip = (round_trip * 10_000 - 2 * TAKER_FEE * 10_000 - SPREAD_BPS) / 2
    if slip < 0:
        raise ValueError("round-trip cost below fees and spread")
    return CostModel(
        fee_rate=TAKER_FEE,
        slippage_bps=slip,
        spread_bps=SPREAD_BPS,
        funding_rate_8h=funding_rate_8h,
    )


def run_config(
    series: BarSeries,
    strategy: str,
    start_ms: int,
    end_ms: int,
    round_trip: float,
    *,
    funding: Mapping[int, float] | None = None,
    capital: float = 10_000.0,
    controller: ControllerLimits | None = None,
    bootstrap: int = 2000,
) -> dict[str, Any]:
    start = int(np.searchsorted(series.ts, start_ms, side="left"))
    end = int(np.searchsorted(series.ts, end_ms, side="left"))
    limits, _grant = resolve(CONSERVATIVE_SCALP)
    limits = replace(limits, round_trip_cost=round_trip, fee_rate=TAKER_FEE)
    ctrl = PortfolioController(capital, controller or ControllerLimits())
    res = run_backtest(
        series,
        STRATEGIES[strategy](),
        capital=capital,
        costs=cost_model(round_trip),
        limits=limits,
        start=start,
        end=end,
        funding_rates=funding,
        entry_guard=ctrl.check_entry,
        on_open=lambda st, f: ctrl.on_open(st, f.symbol, f.side.value, f.price * f.qty, f.ts_ms),
        on_trade=lambda t: ctrl.on_close(t.strategy, t.symbol, t.net, t.exit_ms),
    )
    m = dict(res.metrics)
    returns = [t.net / capital for t in res.trades]
    m["p_value"] = bootstrap_p_value(returns, n=bootstrap, seed=7) if returns else 1.0
    m["rejections"] = res.rejections
    m["kill_switch"] = res.kill_switch
    m["bars"] = end - start
    m["avg_hold_bars"] = (
        float(np.mean([(t.exit_ms - t.entry_ms) / series.interval_ms for t in res.trades]))
        if res.trades
        else 0.0
    )
    m.pop("by_strategy", None)
    return m


__all__ = ["STRATEGIES", "cost_model", "run_config"]
