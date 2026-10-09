"""Out-of-sample validation and the edge verdict.

The rule: **no statistically supported edge after costs means no trade.**

- Anchored walk-forward: the series is cut into ``folds + 1`` blocks. For
  fold ``k`` the parameters are chosen on blocks ``0..k-1`` only and then
  traded on block ``k``, which the choice never saw. Only these out-of-sample
  trades count.
- The verdict needs enough out-of-sample trades, a profit factor and
  expectancy above thresholds, and a one-sided bootstrap test of the mean
  trade return. Its significance level is divided by the number of strategy
  families compared (Bonferroni), so comparing more strategies does not
  make a lucky one look real.
- Each candidate is also run without costs, so the report shows how much of
  a gross edge the fees, slippage and funding eat.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product
from typing import Any

import numpy as np

from jarvis.trading.data import BarSeries, require_usable
from jarvis.trading.engine import BacktestResult, run_backtest
from jarvis.trading.metrics import profit_factor
from jarvis.trading.paper import CostModel, Trade
from jarvis.trading.risk import RiskLimits
from jarvis.trading.strategies import Strategy

StrategyFactory = Callable[..., Strategy]


@dataclass(frozen=True, slots=True)
class EdgeCriteria:
    min_trades: int = 30
    min_profit_factor: float = 1.2
    alpha: float = 0.05
    bootstrap: int = 4000
    seed: int = 7
    min_in_sample_trades: int = 10


@dataclass(frozen=True)
class Verdict:
    family: str
    tradable: bool
    reasons: tuple[str, ...]
    oos_trades: int
    oos_profit_factor: float
    oos_expectancy_r: float
    p_value: float
    alpha_used: float
    chosen_params: tuple[dict[str, Any], ...]
    fold_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def grid(**axes: Sequence[Any]) -> list[dict[str, Any]]:
    keys = list(axes)
    return [dict(zip(keys, values, strict=True)) for values in product(*axes.values())]


def bootstrap_p_value(returns: Sequence[float], *, n: int, seed: int) -> float:
    """One-sided p-value for "mean return <= 0" (bootstrap of the centred sample)."""
    x = np.asarray(returns, dtype=np.float64)
    if len(x) < 2:
        return 1.0
    observed = float(np.mean(x))
    if observed <= 0:
        return 1.0
    rng = np.random.default_rng(seed)
    centred = x - observed
    idx = rng.integers(0, len(x), size=(n, len(x)))
    means = centred[idx].mean(axis=1)
    return float((np.sum(means >= observed) + 1) / (n + 1))


def _score(result: BacktestResult, criteria: EdgeCriteria) -> float:
    """In-sample selection score: risk-adjusted, and only with enough trades."""
    if result.metrics["trades"] < criteria.min_in_sample_trades:
        return float("-inf")
    return float(result.metrics["avg_r"]) * np.sqrt(result.metrics["trades"])


def walk_forward(
    series: BarSeries,
    family: str,
    factory: StrategyFactory,
    params_grid: Sequence[Mapping[str, Any]],
    *,
    folds: int = 4,
    n_families: int = 1,
    criteria: EdgeCriteria | None = None,
    costs: CostModel | None = None,
    limits: RiskLimits | None = None,
    capital: float = 10_000.0,
) -> Verdict:
    criteria = criteria or EdgeCriteria()
    require_usable(series)
    n = len(series)
    block = n // (folds + 1)
    if block < 50 or not params_grid:
        raise ValueError("not enough data for walk-forward validation")
    oos: list[Trade] = []
    chosen: list[dict[str, Any]] = []
    folds_out: list[dict[str, Any]] = []
    for k in range(1, folds + 1):
        train_end, test_end = k * block, (k + 1) * block if k < folds else n
        best: tuple[float, dict[str, Any]] | None = None
        for params in params_grid:
            ins = run_backtest(
                series,
                factory(**params),
                start=0,
                end=train_end,
                costs=costs,
                limits=limits,
                capital=capital,
            )
            score = _score(ins, criteria)
            if best is None or score > best[0]:
                best = (score, dict(params))
        assert best is not None
        if best[0] == float("-inf"):
            folds_out.append({"fold": k, "params": None, "note": "no in-sample candidate"})
            continue
        params = best[1]
        test = run_backtest(
            series,
            factory(**params),
            start=train_end,
            end=test_end,
            costs=costs,
            limits=limits,
            capital=capital,
        )
        chosen.append(params)
        oos.extend(test.trades)
        folds_out.append(
            {
                "fold": k,
                "params": params,
                "trades": test.metrics["trades"],
                "profit_factor": test.metrics["profit_factor"],
                "return_pct": test.metrics["return_pct"],
                "max_drawdown": test.metrics["max_drawdown"],
            }
        )
    return edge_verdict(family, oos, chosen, folds_out, n_families=n_families, criteria=criteria)


def edge_verdict(
    family: str,
    oos: Sequence[Trade],
    chosen: Sequence[dict[str, Any]],
    folds_out: Sequence[dict[str, Any]],
    *,
    n_families: int,
    criteria: EdgeCriteria,
) -> Verdict:
    r = [t.r_multiple for t in oos]
    pf = profit_factor([t.net for t in oos])
    expectancy = float(np.mean(r)) if r else 0.0
    alpha = criteria.alpha / max(1, n_families)
    p = bootstrap_p_value(r, n=criteria.bootstrap, seed=criteria.seed)
    reasons: list[str] = []
    if len(oos) < criteria.min_trades:
        reasons.append(f"only {len(oos)} out-of-sample trades (need {criteria.min_trades})")
    if pf < criteria.min_profit_factor:
        reasons.append(f"profit factor {pf:.2f} below {criteria.min_profit_factor}")
    if expectancy <= 0:
        reasons.append("no positive expectancy after costs")
    if p > alpha:
        reasons.append(f"not significant (p={p:.3f} > {alpha:.4f})")
    return Verdict(
        family,
        not reasons,
        tuple(reasons) or ("edge supported out of sample",),
        len(oos),
        pf,
        expectancy,
        p,
        alpha,
        tuple(chosen),
        tuple(folds_out),
    )


@dataclass(frozen=True)
class Comparison:
    verdicts: tuple[Verdict, ...]
    best: Verdict | None  # None: no family has a supported edge -> do not trade

    @property
    def decision(self) -> str:
        return "trade" if self.best else "no_trade"


def compare(
    series: BarSeries,
    families: Mapping[str, tuple[StrategyFactory, Sequence[Mapping[str, Any]]]],
    **kw: Any,
) -> Comparison:
    """Walk-forward every family; pick the best supported one, or none."""
    verdicts = tuple(
        walk_forward(series, name, factory, g, n_families=len(families), **kw)
        for name, (factory, g) in families.items()
    )
    supported = [v for v in verdicts if v.tradable]
    best = max(supported, key=lambda v: v.oos_expectancy_r, default=None)
    return Comparison(verdicts, best)


__all__ = [
    "Comparison",
    "EdgeCriteria",
    "Verdict",
    "bootstrap_p_value",
    "compare",
    "edge_verdict",
    "grid",
    "walk_forward",
]
