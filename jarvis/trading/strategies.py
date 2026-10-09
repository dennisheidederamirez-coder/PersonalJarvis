"""Rule-based strategies. A strategy only states a TARGET (long, short, flat or
"no change") with a stop and take-profit at a bar's close; it never sizes,
never executes and never sees the account. Indicators are causal, so a
decision at bar ``i`` depends on bars ``<= i`` only.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import numpy as np

from jarvis.trading.data import BarSeries
from jarvis.trading.indicators import atr, prior_high, prior_low, rsi, sma


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True, slots=True)
class Target:
    """What the strategy wants after bar ``i`` closes. ``side=None`` = be flat."""

    side: Side | None
    stop: float | None = None
    take_profit: float | None = None
    reason: str = ""


class Strategy(Protocol):
    name: str
    params: Mapping[str, Any]

    def prepare(self, series: BarSeries) -> None: ...

    def decide(self, i: int, current: Side | None) -> Target | None:
        """``None`` keeps the current position as it is."""
        ...


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


class _AtrExits:
    """Shared ATR-based stop / take-profit placement."""

    stop_atr: float
    tp_atr: float
    _atr: Any
    _close: Any

    def _target(self, i: int, side: Side, reason: str) -> Target | None:
        a = float(self._atr[i])
        c = float(self._close[i])
        if not _finite(a, c) or a <= 0:
            return None
        s = side.sign
        return Target(side, c - s * self.stop_atr * a, c + s * self.tp_atr * a, reason)


class SmaCross(_AtrExits):
    """Trend following: long while the fast SMA is above the slow one, short
    (if allowed) while below."""

    def __init__(
        self,
        fast: int = 20,
        slow: int = 50,
        *,
        atr_n: int = 14,
        stop_atr: float = 2.0,
        tp_atr: float = 4.0,
        allow_short: bool = True,
        name: str | None = None,
    ) -> None:
        if fast >= slow:
            raise ValueError("fast must be shorter than slow")
        self.name = name or "sma_cross"
        self.params: Mapping[str, Any] = {
            "fast": fast,
            "slow": slow,
            "stop_atr": stop_atr,
            "tp_atr": tp_atr,
            "allow_short": allow_short,
        }
        self.fast, self.slow, self.atr_n = fast, slow, atr_n
        self.stop_atr, self.tp_atr, self.allow_short = stop_atr, tp_atr, allow_short

    def prepare(self, series: BarSeries) -> None:
        self._close = series.close
        self._fast = sma(series.close, self.fast)
        self._slow = sma(series.close, self.slow)
        self._atr = atr(series.high, series.low, series.close, self.atr_n)

    def decide(self, i: int, current: Side | None) -> Target | None:
        if i < 1:
            return None
        f, s, pf, ps = (
            float(self._fast[i]),
            float(self._slow[i]),
            float(self._fast[i - 1]),
            float(self._slow[i - 1]),
        )
        if not _finite(f, s, pf, ps):
            return None
        crossed_up = f > s and pf <= ps
        crossed_down = f < s and pf >= ps
        if crossed_up and current is not Side.LONG:
            return self._target(i, Side.LONG, "fast SMA crossed above slow SMA")
        if crossed_down and current is not Side.SHORT:
            if self.allow_short:
                return self._target(i, Side.SHORT, "fast SMA crossed below slow SMA")
            if current is Side.LONG:
                return Target(None, reason="fast SMA crossed below slow SMA")
        return None


class DonchianBreakout(_AtrExits):
    """Breakout: enter on a close beyond the prior ``entry_n``-bar range, leave
    on a close beyond the opposite ``exit_n``-bar range (or stop / target)."""

    def __init__(
        self,
        entry_n: int = 55,
        exit_n: int = 20,
        *,
        atr_n: int = 14,
        stop_atr: float = 2.0,
        tp_atr: float = 6.0,
        allow_short: bool = True,
        name: str | None = None,
    ) -> None:
        self.name = name or "donchian_breakout"
        self.params: Mapping[str, Any] = {
            "entry_n": entry_n,
            "exit_n": exit_n,
            "stop_atr": stop_atr,
            "tp_atr": tp_atr,
            "allow_short": allow_short,
        }
        self.entry_n, self.exit_n, self.atr_n = entry_n, exit_n, atr_n
        self.stop_atr, self.tp_atr, self.allow_short = stop_atr, tp_atr, allow_short

    def prepare(self, series: BarSeries) -> None:
        self._close = series.close
        self._up = prior_high(series.high, self.entry_n)
        self._down = prior_low(series.low, self.entry_n)
        self._exit_low = prior_low(series.low, self.exit_n)
        self._exit_high = prior_high(series.high, self.exit_n)
        self._atr = atr(series.high, series.low, series.close, self.atr_n)

    def decide(self, i: int, current: Side | None) -> Target | None:
        c = float(self._close[i])
        up, down = float(self._up[i]), float(self._down[i])
        if not _finite(c, up, down):
            return None
        if c > up and current is not Side.LONG:
            return self._target(i, Side.LONG, f"close broke the {self.entry_n}-bar high")
        if c < down and current is not Side.SHORT and self.allow_short:
            return self._target(i, Side.SHORT, f"close broke the {self.entry_n}-bar low")
        if current is Side.LONG and c < float(self._exit_low[i]):
            return Target(None, reason=f"close fell below the {self.exit_n}-bar low")
        if current is Side.SHORT and c > float(self._exit_high[i]):
            return Target(None, reason=f"close rose above the {self.exit_n}-bar high")
        return None


class RsiReversion(_AtrExits):
    """Mean reversion: buy oversold, sell overbought; leave when RSI is back at 50."""

    def __init__(
        self,
        n: int = 14,
        low: float = 30.0,
        high: float = 70.0,
        *,
        atr_n: int = 14,
        stop_atr: float = 1.5,
        tp_atr: float = 2.0,
        allow_short: bool = True,
        name: str | None = None,
    ) -> None:
        self.name = name or "rsi_reversion"
        self.params: Mapping[str, Any] = {
            "n": n,
            "low": low,
            "high": high,
            "stop_atr": stop_atr,
            "tp_atr": tp_atr,
            "allow_short": allow_short,
        }
        self.n, self.low, self.high, self.atr_n = n, low, high, atr_n
        self.stop_atr, self.tp_atr, self.allow_short = stop_atr, tp_atr, allow_short

    def prepare(self, series: BarSeries) -> None:
        self._close = series.close
        self._rsi = rsi(series.close, self.n)
        self._atr = atr(series.high, series.low, series.close, self.atr_n)

    def decide(self, i: int, current: Side | None) -> Target | None:
        r = float(self._rsi[i])
        if not math.isfinite(r):
            return None
        if current is None:
            if r < self.low:
                return self._target(i, Side.LONG, f"RSI {r:.0f} below {self.low:.0f}")
            if r > self.high and self.allow_short:
                return self._target(i, Side.SHORT, f"RSI {r:.0f} above {self.high:.0f}")
            return None
        if (current is Side.LONG and r >= 50) or (current is Side.SHORT and r <= 50):
            return Target(None, reason="RSI back at 50")
        return None


class AlwaysFlat:
    """The do-nothing baseline every strategy must beat after costs."""

    name = "flat"
    params: Mapping[str, Any] = {}

    def prepare(self, series: BarSeries) -> None:
        return None

    def decide(self, i: int, current: Side | None) -> Target | None:
        return Target(None, reason="baseline") if current is not None else None


def stop_on_correct_side(side: Side, entry: float, stop: float) -> bool:
    return bool(np.sign(entry - stop) == side.sign)


__all__ = [
    "AlwaysFlat",
    "DonchianBreakout",
    "RsiReversion",
    "Side",
    "SmaCross",
    "Strategy",
    "Target",
    "stop_on_correct_side",
]
