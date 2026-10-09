"""Performance figures that count costs and risk, not just wins."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.paper import Trade

#: Profit factor without a single losing trade is reported as this cap.
PF_CAP = 99.0


def max_drawdown(equity: NDArray[np.float64]) -> float:
    """Largest fall from a running peak, as a fraction (0.25 = -25 %)."""
    if len(equity) == 0:
        return 0.0
    peaks = np.maximum.accumulate(equity)
    return float(np.max((peaks - equity) / peaks))


def profit_factor(nets: Sequence[float]) -> float:
    gains = sum(x for x in nets if x > 0)
    losses = -sum(x for x in nets if x < 0)
    if losses == 0:
        return PF_CAP if gains > 0 else 0.0
    return min(PF_CAP, gains / losses)


def compute(
    trades: Sequence[Trade],
    equity: NDArray[np.float64],
    *,
    capital: float,
    periods_per_year: float,
) -> dict[str, Any]:
    nets = [t.net for t in trades]
    n = len(trades)
    wins = sum(1 for x in nets if x > 0)
    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.zeros(0)
    mean, std = (
        (float(np.mean(returns)), float(np.std(returns, ddof=1)))
        if len(returns) > 1
        else (0.0, 0.0)
    )
    downside = returns[returns < 0]
    dstd = float(np.sqrt(np.mean(downside**2))) if len(downside) else 0.0
    ann = math.sqrt(periods_per_year)
    final = float(equity[-1]) if len(equity) else capital
    return {
        "trades": n,
        "wins": wins,
        "hit_rate": wins / n if n else 0.0,
        "profit_factor": profit_factor(nets),
        "expectancy": float(np.mean(nets)) if n else 0.0,
        "avg_r": float(np.mean([t.r_multiple for t in trades])) if n else 0.0,
        "net_pnl": final - capital,
        "return_pct": final / capital - 1,
        "fees": sum(t.fees for t in trades),
        "slippage": sum(t.slippage for t in trades),
        "funding": sum(t.funding for t in trades),
        "max_drawdown": max_drawdown(equity),
        "sharpe": mean / std * ann if std > 0 else 0.0,
        "sortino": mean / dstd * ann if dstd > 0 else 0.0,
        "long_trades": sum(1 for t in trades if t.side.value == "long"),
        "short_trades": sum(1 for t in trades if t.side.value == "short"),
    }


__all__ = ["PF_CAP", "compute", "max_drawdown", "profit_factor"]
