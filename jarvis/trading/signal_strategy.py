"""A strategy built on external signals (e.g. TradingView alerts).

It is an ordinary rule-based strategy: it enters in the signal's direction
with ATR-based stop and target, leaves on an opposite signal or after
``hold_bars``, and it is validated and ranked like every other strategy. It
cannot trade until its own walk-forward verdict says so.

Timing is conservative: a signal can only be acted on at the first bar
CLOSE at or after the moment Jarvis received it (never at the bar it refers
to, if it arrived later), and the order fills at the following open.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from jarvis.trading.data import BarSeries
from jarvis.trading.indicators import atr
from jarvis.trading.setups import symbol_matches
from jarvis.trading.signals import ExternalSignal
from jarvis.trading.strategies import Side, Target, _AtrExits

_UNIT_MIN = {"S": 1 / 60, "D": 1440, "W": 10_080, "M": 43_200}


def interval_minutes(interval: str) -> float:
    """TradingView interval string -> minutes ("60" -> 60, "1D"/"D" -> 1440)."""
    text = interval.strip().upper()
    if text and text[-1] in _UNIT_MIN:
        count = int(text[:-1] or 1)
        return count * _UNIT_MIN[text[-1]]
    return float(int(text))


class SignalStrategy(_AtrExits):
    def __init__(
        self,
        signals: Iterable[ExternalSignal],
        *,
        name: str = "external_signal",
        indicator: str | None = None,
        signal_names: Iterable[str] | None = None,
        hold_bars: int = 24,
        atr_n: int = 14,
        stop_atr: float = 2.0,
        tp_atr: float = 3.0,
    ) -> None:
        self.name = name
        self._signals = list(signals)
        self.indicator = indicator
        self.signal_names = frozenset(signal_names) if signal_names else None
        self.hold_bars, self.atr_n = hold_bars, atr_n
        self.stop_atr, self.tp_atr = stop_atr, tp_atr
        self.params: Mapping[str, Any] = {
            "indicator": indicator,
            "signals": sorted(self.signal_names or ()),
            "hold_bars": hold_bars,
            "stop_atr": stop_atr,
            "tp_atr": tp_atr,
        }

    def prepare(self, series: BarSeries) -> None:
        self._close = series.close
        self._atr = atr(series.high, series.low, series.close, self.atr_n)
        n = len(series)
        closes = series.ts + series.interval_ms  # close time of every bar
        minutes = series.interval_ms / 60_000
        self._at: dict[int, Side] = {}
        conflicted: set[int] = set()
        for s in self._signals:
            if s.direction == "neutral" or not symbol_matches(s.symbol, series.instrument):
                continue
            if self.indicator and s.indicator != self.indicator:
                continue
            if self.signal_names and s.signal not in self.signal_names:
                continue
            if abs(interval_minutes(s.interval) - minutes) > 1e-9:
                continue  # a signal from another timeframe is a different strategy
            k = int(np.searchsorted(closes, s.received_at_ms, side="left"))
            if k >= n:
                continue
            side = Side.LONG if s.direction == "long" else Side.SHORT
            if k in self._at and self._at[k] is not side:
                conflicted.add(k)
            self._at[k] = side
        for k in conflicted:  # contradicting signals on one bar: no information
            del self._at[k]
        self._last = np.full(n, -1, dtype=np.int64)
        last = -1
        for i in range(n):
            if i in self._at:
                last = i
            self._last[i] = last

    def decide(self, i: int, current: Side | None) -> Target | None:
        side = self._at.get(i)
        if side is not None and side is not current:
            return self._target(i, side, f"{self.name}: {side.value} signal")
        if current is not None:
            last = int(self._last[i])
            if last < 0 or i - last >= self.hold_bars:
                return Target(None, reason=f"held {self.hold_bars} bars without a new signal")
        return None


__all__ = ["SignalStrategy", "interval_minutes"]
