"""Three scalping candidates for 1m-5m bars. Each only states a target with
an ATR stop and target plus a maximum holding time; sizing, limits and
execution stay with the risk manager, the controller and the simulator.
Signals use completed data only (causal indicators, completed-period levels).

TradingView-only indicators (e.g. proprietary wave/momentum tools) are not
re-implemented; their alerts can enter as an external-signal strategy
(``signal_strategy.SignalStrategy``) — optional, never required.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.data import BarSeries
from jarvis.trading.indicators import atr, ema, prior_high, prior_low, pvsra, sma
from jarvis.trading.scalping.levels import levels
from jarvis.trading.strategies import Side, Target


class _Scalp:
    """Shared plumbing: a precomputed signal array (+1 / -1 / 0), ATR exits,
    exit on an opposite signal, and a time stop after ``max_hold`` bars."""

    name: str
    params: Mapping[str, Any]
    stop_atr: float
    tp_atr: float
    max_hold: int
    _close: NDArray[np.float64]
    _atr: NDArray[np.float64]
    _sig: NDArray[np.int8]
    _why: list[str]

    def _finish(self, series: BarSeries, sig: NDArray[np.int8], why: list[str]) -> None:
        self._close = series.close
        self._atr = atr(series.high, series.low, series.close, 14)
        self._sig, self._why = sig, why
        last = np.full(len(sig), -1, dtype=np.int64)
        current = -1
        for k in range(len(sig)):
            if sig[k] != 0:
                current = k
            last[k] = current
        self._last = last

    def decide(self, i: int, current: Side | None) -> Target | None:
        s = int(self._sig[i])
        a, c = float(self._atr[i]), float(self._close[i])
        if s != 0 and (current is None or current.sign != s) and math.isfinite(a) and a > 0:
            side = Side.LONG if s > 0 else Side.SHORT
            return Target(side, c - s * self.stop_atr * a, c + s * self.tp_atr * a, self._why[i])
        if current is not None and i - int(self._last[i]) >= self.max_hold:
            return Target(None, reason=f"time stop after {self.max_hold} bars")
        return None

    def explain(self, i: int, current: Side | None) -> str:
        if current is not None:
            return f"holding {current.value}: no exit condition yet"
        return self._why[i] or "no signal"


class ScalpBreakout(_Scalp):
    """Momentum breakout: a close beyond the prior ``lookback``-bar range on a
    volume surge (volume >= ``vol_mult`` x its ``vol_n``-bar average)."""

    def __init__(
        self,
        lookback: int = 20,
        vol_mult: float = 2.0,
        vol_n: int = 20,
        *,
        stop_atr: float = 1.0,
        tp_atr: float = 2.0,
        max_hold: int = 30,
        allow_short: bool = True,
        name: str | None = None,
    ) -> None:
        self.name = name or "scalp_breakout"
        self.params = {
            "lookback": lookback,
            "vol_mult": vol_mult,
            "vol_n": vol_n,
            "stop_atr": stop_atr,
            "tp_atr": tp_atr,
            "max_hold": max_hold,
            "allow_short": allow_short,
        }
        self.lookback, self.vol_mult, self.vol_n = lookback, vol_mult, vol_n
        self.stop_atr, self.tp_atr, self.max_hold = stop_atr, tp_atr, max_hold
        self.allow_short = allow_short

    def prepare(self, series: BarSeries) -> None:
        up, dn = prior_high(series.high, self.lookback), prior_low(series.low, self.lookback)
        avg = np.concatenate(([np.nan], sma(series.volume, self.vol_n)[:-1]))  # prior bars
        sig = np.zeros(len(series), dtype=np.int8)
        why = [""] * len(series)
        for k in range(len(series)):
            c, v = float(series.close[k]), float(series.volume[k])
            if not (math.isfinite(up[k]) and math.isfinite(avg[k])):
                why[k] = "warming up"
                continue
            surge = v >= self.vol_mult * avg[k]
            if c > up[k] and surge:
                sig[k], why[k] = (
                    1,
                    f"breakout above the {self.lookback}-bar high on {v / avg[k]:.1f}x volume",
                )
            elif c < dn[k] and surge and self.allow_short:
                sig[k], why[k] = (
                    -1,
                    f"breakdown below the {self.lookback}-bar low on {v / avg[k]:.1f}x volume",
                )
            elif c > up[k] or c < dn[k]:
                why[k] = f"range break without volume ({v / avg[k]:.1f}x < {self.vol_mult}x)"
            else:
                why[k] = f"no breakout: close inside the {self.lookback}-bar range"
        self._finish(series, sig, why)


class EmaTrendScalp(_Scalp):
    """Trend pullback: EMA``fast`` above EMA``slow`` (long) or below (short);
    enter when a bar dips to the fast EMA and closes back in the trend
    direction on at least average volume."""

    def __init__(
        self,
        fast: int = 50,
        slow: int = 200,
        vol_n: int = 20,
        vol_mult: float = 1.0,
        touch_atr: float = 0.25,
        *,
        stop_atr: float = 1.5,
        tp_atr: float = 3.0,
        max_hold: int = 60,
        allow_short: bool = True,
        name: str | None = None,
    ) -> None:
        self.name = name or "ema_trend_scalp"
        self.params = {
            "fast": fast,
            "slow": slow,
            "vol_n": vol_n,
            "vol_mult": vol_mult,
            "touch_atr": touch_atr,
            "stop_atr": stop_atr,
            "tp_atr": tp_atr,
            "max_hold": max_hold,
            "allow_short": allow_short,
        }
        self.fast, self.slow, self.vol_n, self.vol_mult = fast, slow, vol_n, vol_mult
        self.touch_atr, self.allow_short = touch_atr, allow_short
        self.stop_atr, self.tp_atr, self.max_hold = stop_atr, tp_atr, max_hold

    def prepare(self, series: BarSeries) -> None:
        f, s = ema(series.close, self.fast), ema(series.close, self.slow)
        a = atr(series.high, series.low, series.close, 14)
        avg = np.concatenate(([np.nan], sma(series.volume, self.vol_n)[:-1]))
        sig = np.zeros(len(series), dtype=np.int8)
        why = [""] * len(series)
        for k in range(len(series)):
            if not all(math.isfinite(x) for x in (f[k], s[k], a[k], avg[k])):
                why[k] = "warming up"
                continue
            o, h, lo, c, v = (
                float(series.open[k]),
                float(series.high[k]),
                float(series.low[k]),
                float(series.close[k]),
                float(series.volume[k]),
            )
            near = self.touch_atr * a[k]
            volume_ok = v >= self.vol_mult * avg[k]
            if f[k] > s[k]:
                touched = lo <= f[k] + near and c > f[k] and c > o
                if touched and volume_ok:
                    sig[k], why[k] = 1, "uptrend pullback to EMA held on volume"
                else:
                    why[k] = (
                        "uptrend, no pullback to the fast EMA"
                        if not touched
                        else "uptrend pullback without volume"
                    )
            elif f[k] < s[k] and self.allow_short:
                touched = h >= f[k] - near and c < f[k] and c < o
                if touched and volume_ok:
                    sig[k], why[k] = -1, "downtrend pullback to EMA rejected on volume"
                else:
                    why[k] = (
                        "downtrend, no pullback to the fast EMA"
                        if not touched
                        else "downtrend pullback without volume"
                    )
            else:
                why[k] = "no trend (fast EMA equals slow EMA)"
        self._finish(series, sig, why)


class OrderflowLevelScalp(_Scalp):
    """Order flow at reference levels: a PVSRA climax bar near the daily open
    or the previous day/week high/low, with aggressive volume in the bar's
    direction. Needs a source that reports aggressor volume; without it
    there is no signal (never an estimate)."""

    def __init__(
        self,
        near_atr: float = 0.5,
        delta_frac: float = 0.2,
        *,
        stop_atr: float = 1.0,
        tp_atr: float = 2.0,
        max_hold: int = 30,
        allow_short: bool = True,
        name: str | None = None,
    ) -> None:
        self.name = name or "orderflow_level_scalp"
        self.params = {
            "near_atr": near_atr,
            "delta_frac": delta_frac,
            "stop_atr": stop_atr,
            "tp_atr": tp_atr,
            "max_hold": max_hold,
            "allow_short": allow_short,
        }
        self.near_atr, self.delta_frac, self.allow_short = near_atr, delta_frac, allow_short
        self.stop_atr, self.tp_atr, self.max_hold = stop_atr, tp_atr, max_hold

    def prepare(self, series: BarSeries) -> None:
        n = len(series)
        sig = np.zeros(n, dtype=np.int8)
        why = [""] * n
        if series.taker_buy is None:
            self._finish(series, sig, ["no aggressor volume from this source"] * n)
            return
        cls = pvsra(series.open, series.high, series.low, series.close, series.volume)
        lv = levels(series)
        a = atr(series.high, series.low, series.close, 14)
        delta = np.where(
            series.volume > 0, (2 * series.taker_buy - series.volume) / series.volume, 0.0
        )
        for k in range(n):
            if not math.isfinite(a[k]):
                why[k] = "warming up"
                continue
            c = int(cls[k])
            if abs(c) < 2:
                why[k] = "no PVSRA climax bar"
                continue
            refs = {name: val for name, val in lv.at(k).items() if math.isfinite(val)}
            near = [
                name
                for name, val in refs.items()
                if abs(float(series.close[k]) - val) <= self.near_atr * a[k]
            ]
            if not near:
                why[k] = "climax bar away from every reference level"
                continue
            d = float(delta[k])
            if c > 0 and d >= self.delta_frac:
                sig[k], why[k] = 1, f"bull climax at {near[0]} with buy delta {d:+.0%}"
            elif c < 0 and d <= -self.delta_frac and self.allow_short:
                sig[k], why[k] = -1, f"bear climax at {near[0]} with sell delta {d:+.0%}"
            else:
                why[k] = f"climax at {near[0]} but aggressor delta {d:+.0%} does not confirm"
        self._finish(series, sig, why)


__all__ = ["EmaTrendScalp", "OrderflowLevelScalp", "ScalpBreakout"]
