"""Scalping candidates, version 2: limit-order entries, larger moves, holds
of 5 to 30 minutes.

Stops and targets are scaled by the ATR of the last CLOSED 15-minute bar,
not the 1-minute ATR. A 1-minute ATR target is smaller than the round-trip
cost, which is the phase-1 lesson. ``execsim`` also refuses any setup whose
target is under a multiple of the scenario's round-trip cost.

Each candidate exists in two execution forms: ``maker`` (a resting limit at a
level) and ``taker`` (a market order at the next open). Optional filters:

- ``session``: 07–21 UTC only (Europe and US hours);
- ``vol``: only when the 15m ATR is above its average over the prior day.

All inputs are causal: indicators up to the signal bar, a 15m bar only after
it has closed, and reference levels from completed periods.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.data import BarSeries
from jarvis.trading.indicators import atr, ema, prior_high, prior_low, pvsra, sma
from jarvis.trading.scalping.execsim import Setup
from jarvis.trading.scalping.levels import levels
from jarvis.trading.timeframes import align, resample

M15 = 15 * 60_000
FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Params:
    entry: str = "maker"  # maker / taker
    target_atr: float = 1.0  # in 15m ATR
    stop_atr: float = 0.75
    session: bool = False
    vol: bool = False
    max_hold_min: int = 30
    ttl_min: int = 5
    mode: str = "continuation"  # order flow only: continuation / absorption


@dataclass(frozen=True)
class Context:
    """Per-bar inputs shared by all candidates (computed once per series)."""

    atr15: FloatArray  # last closed 15m ATR, aligned to the signal bars
    vol_ok: NDArray[np.bool_]
    hour: NDArray[np.int64]
    ema20: FloatArray
    ema50: FloatArray
    ema200: FloatArray
    vol_avg: FloatArray  # prior 20 bars
    buy_share20: FloatArray  # aggressive buy share over the last 20 bars
    delta: FloatArray  # per-bar aggressor delta in [-1, 1]
    pvsra: NDArray[np.int8]
    hi20: FloatArray
    lo20: FloatArray
    level_values: FloatArray  # (n, 5) completed-period reference levels
    trend15: NDArray[np.int8]  # +1 / -1 / 0 from 15m EMA50 vs EMA200


def context(series: BarSeries) -> Context:
    n = len(series)
    m15 = resample(series, M15) if series.interval_ms < M15 else series
    idx = align(series, m15)
    a15 = atr(m15.high, m15.low, m15.close, 14)
    atr15 = np.where(idx >= 0, a15[np.clip(idx, 0, None)], np.nan)
    rel = atr15 / series.close
    per_day = max(1, 86_400_000 // series.interval_ms)
    day_avg = np.concatenate(([np.nan], sma(np.nan_to_num(rel), per_day)[:-1]))
    e50_15, e200_15 = ema(m15.close, 50), ema(m15.close, 200)
    t15 = np.sign(np.nan_to_num(e50_15 - e200_15)).astype(np.int8)  # warm-up = no trend
    trend15 = np.where(idx >= 0, t15[np.clip(idx, 0, None)], 0).astype(np.int8)
    buys = series.taker_buy if series.taker_buy is not None else np.full(n, np.nan)
    vsum = sma(series.volume, 20) * 20
    bsum = sma(buys, 20) * 20
    lv = levels(series)
    return Context(
        atr15=atr15,
        vol_ok=np.nan_to_num(rel) > np.nan_to_num(day_avg, nan=np.inf),
        hour=(series.ts // 3_600_000) % 24,
        ema20=ema(series.close, 20),
        ema50=ema(series.close, 50),
        ema200=ema(series.close, 200),
        vol_avg=np.concatenate(([np.nan], sma(series.volume, 20)[:-1])),
        buy_share20=np.where(vsum > 0, bsum / vsum, np.nan),
        delta=np.divide(
            2 * buys - series.volume, series.volume, out=np.zeros(n), where=series.volume > 0
        ),
        pvsra=pvsra(series.open, series.high, series.low, series.close, series.volume),
        hi20=prior_high(series.high, 20),
        lo20=prior_low(series.low, 20),
        level_values=np.column_stack(
            [lv.daily_open, lv.prev_day_high, lv.prev_day_low, lv.prev_week_high, lv.prev_week_low]
        ),
        trend15=trend15,
    )


def _bars(minutes: int, series: BarSeries) -> int:
    return max(1, round(minutes * 60_000 / series.interval_ms))


def _gate(cx: Context, k: int, p: Params) -> bool:
    if not math.isfinite(cx.atr15[k]) or cx.atr15[k] <= 0:
        return False
    if p.session and not 7 <= int(cx.hour[k]) < 21:
        return False
    return not (p.vol and not bool(cx.vol_ok[k]))


def _setup(
    series: BarSeries, cx: Context, k: int, side: int, p: Params, limit: float, reason: str
) -> Setup:
    a = float(cx.atr15[k])
    ref = limit if p.entry == "maker" else float(series.close[k])
    return Setup(
        k,
        side,
        "maker" if p.entry == "maker" else "taker",
        stop=ref - side * p.stop_atr * a,
        target=ref + side * p.target_atr * a,
        limit=limit if p.entry == "maker" else None,
        ttl=_bars(p.ttl_min, series),
        max_hold=_bars(p.max_hold_min, series),
        reason=reason,
    )


def breakout(series: BarSeries, cx: Context, p: Params) -> list[Setup]:
    """Momentum breakout on a volume surge. Maker: a limit at the broken level
    (the retest). Taker: a market order at the next open."""
    o, h, lo, c, v = series.open, series.high, series.low, series.close, series.volume
    out: list[Setup] = []
    for k in range(len(series)):
        if not _gate(cx, k, p) or not math.isfinite(cx.hi20[k]) or not math.isfinite(cx.vol_avg[k]):
            continue
        rng = h[k] - lo[k]
        if rng <= 0 or v[k] < 2.0 * cx.vol_avg[k] or abs(c[k] - o[k]) < 0.5 * rng:
            continue
        if c[k] > cx.hi20[k] and c[k] > o[k]:
            out.append(_setup(series, cx, k, 1, p, float(cx.hi20[k]), "breakout up on volume"))
        elif c[k] < cx.lo20[k] and c[k] < o[k]:
            out.append(_setup(series, cx, k, -1, p, float(cx.lo20[k]), "breakdown on volume"))
    return out


def trend_pullback(series: BarSeries, cx: Context, p: Params) -> list[Setup]:
    """EMA50/EMA200 trend with a rising EMA50 and aggressive volume on the
    trend side. Maker: when price is 0.25–1 ATR away from EMA20, a limit at
    EMA20 (the pullback). Taker: a bar that dipped to EMA20 and closed back
    in the trend direction."""
    o, h, lo, c = series.open, series.high, series.low, series.close
    out: list[Setup] = []
    for k in range(5, len(series)):
        if (
            not _gate(cx, k, p)
            or not math.isfinite(cx.ema200[k])
            or not math.isfinite(cx.buy_share20[k])
        ):
            continue
        a, e20 = float(cx.atr15[k]), float(cx.ema20[k])
        up = (
            cx.ema50[k] > cx.ema200[k]
            and cx.ema50[k] > cx.ema50[k - 5]
            and cx.buy_share20[k] >= 0.52
        )
        dn = (
            cx.ema50[k] < cx.ema200[k]
            and cx.ema50[k] < cx.ema50[k - 5]
            and cx.buy_share20[k] <= 0.48
        )
        if not (up or dn):
            continue
        side = 1 if up else -1
        if p.entry == "maker":
            gap = side * (c[k] - e20)
            if 0.25 * a <= gap <= 1.0 * a and side * (lo[k] if up else h[k]) > side * e20:
                out.append(
                    _setup(series, cx, k, side, p, e20, "trend: limit at the EMA20 pullback")
                )
        else:
            touched = (lo[k] <= e20 + 0.1 * a) if up else (h[k] >= e20 - 0.1 * a)
            if touched and side * (c[k] - e20) > 0 and side * (c[k] - o[k]) > 0:
                out.append(_setup(series, cx, k, side, p, e20, "trend: EMA20 pullback held"))
    return out


def orderflow(series: BarSeries, cx: Context, p: Params) -> list[Setup]:
    """A PVSRA climax bar within 0.5 ATR of a completed-period level, with
    aggressor delta of at least 20 %.

    - ``continuation``: trade in the direction of the bar and its delta.
    - ``absorption``: heavy aggressive selling (buying) that closes in the
      upper (lower) half of the bar — the flow was absorbed — so trade
      against it.

    Maker: a limit at the bar's midpoint. Taker: the next open."""
    o, h, lo, c = series.open, series.high, series.low, series.close
    out: list[Setup] = []
    if series.taker_buy is None:
        return out
    for k in range(len(series)):
        if not _gate(cx, k, p) or abs(int(cx.pvsra[k])) < 2:
            continue
        a = float(cx.atr15[k])
        lv = cx.level_values[k]
        near = np.isfinite(lv) & (np.abs(lv - c[k]) <= 0.5 * a)
        if not near.any():
            continue
        d, rng = float(cx.delta[k]), h[k] - lo[k]
        if rng <= 0:
            continue
        pos = (c[k] - lo[k]) / rng
        side = 0
        if p.mode == "continuation":
            if c[k] > o[k] and d >= 0.2:
                side = 1
            elif c[k] < o[k] and d <= -0.2:
                side = -1
        else:
            if d <= -0.2 and pos >= 0.5:
                side = 1
            elif d >= 0.2 and pos <= 0.5:
                side = -1
        if side:
            out.append(
                _setup(
                    series,
                    cx,
                    k,
                    side,
                    p,
                    float((h[k] + lo[k]) / 2),
                    f"order flow {p.mode} at a level (delta {d:+.0%})",
                )
            )
    return out


CANDIDATES = {"breakout": breakout, "trend_pullback": trend_pullback, "orderflow": orderflow}

__all__ = ["CANDIDATES", "Context", "Params", "breakout", "context", "orderflow", "trend_pullback"]
