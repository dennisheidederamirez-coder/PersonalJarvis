"""Bar-level execution with separate maker and taker models.

Historic order-book data does not exist for free, so bar data is all we have
for years of history. These rules keep a bar fill from being better than the
market could have allowed. They are MODELLED, not observed: the scenario
parameters bracket what the order-book recorder will later measure.

Taker (market) orders
    A signal is known at a bar's close. The order executes at the OPEN of the
    bar ``1 + latency_bars`` later, and pays half the spread, ``taker_slip_bps``
    and the taker fee.

Maker (limit) orders
    The order rests from the next bar for ``ttl`` bars. It fills only when
    price trades THROUGH the limit by ``through_bps``; a mere touch is not a
    fill, because the queue ahead may not clear. Penetration inside
    ``partial_band_bps`` beyond that fills a fraction, and the rest is
    cancelled. Unfilled orders expire. The fill price is the limit, minus an
    ``adverse_bps`` haircut for adverse selection, plus the maker fee. A
    fill is never at a better price than the limit.

Exits
    - A take-profit rests as a maker limit and fills only in a LATER bar, by
      the same trade-through rule.
    - A stop is a stop-market order (taker). On a gap through the stop it
      fills at the bar's open, otherwise at the stop, with slippage either way.
    - A time stop exits as a taker at the next bar's open.
    - When one bar touches both the stop and the target, the stop counts
      (worst case).

Sizing: 0.5 % of current equity at risk, INCLUDING the scenario's
round-trip cost, capped at 1x notional and at ``max_participation`` of the
fill bar's volume. A ``PortfolioController`` can veto every entry.

The 10 % kill switch is recorded with its date. The run then continues, so
statistics cover the whole period; the stop is reported, not hidden.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from jarvis.trading.data import BarSeries
from jarvis.trading.scalping.controller import ControllerLimits, PortfolioController

FUNDING_MS = 8 * 3_600_000


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    spread_bps: float = 0.5
    taker_slip_bps: float = 1.5
    through_bps: float = 1.5
    partial_band_bps: float = 2.0
    adverse_bps: float = 0.5
    latency_bars: int = 0
    max_participation: float = 0.01

    def taker_cost(self) -> float:
        """One taker fill as a share of price (fee + half spread + slippage)."""
        return self.taker_fee + (self.spread_bps / 2 + self.taker_slip_bps) / 10_000

    def maker_cost(self) -> float:
        return self.maker_fee + self.adverse_bps / 10_000

    def round_trip(self, entry: str) -> float:
        """Entry, plus a stop exit as a taker (the expensive case)."""
        return (self.maker_cost() if entry == "maker" else self.taker_cost()) + self.taker_cost()


#: Pre-registered scenarios. Calibrate against the order-book recorder later.
OPTIMISTIC = Scenario(
    "optimistic",
    spread_bps=0.2,
    taker_slip_bps=0.5,
    through_bps=0.5,
    partial_band_bps=0.0,
    adverse_bps=0.0,
)
BASE = Scenario("base")
HARSH = Scenario(
    "harsh",
    spread_bps=1.0,
    taker_slip_bps=4.0,
    through_bps=3.0,
    partial_band_bps=4.0,
    adverse_bps=2.0,
    latency_bars=1,
)
SCENARIOS: Mapping[str, Scenario] = {s.name: s for s in (OPTIMISTIC, BASE, HARSH)}


@dataclass(frozen=True, slots=True)
class Setup:
    """A trade idea known at the close of bar ``i``."""

    i: int
    side: int  # +1 long, -1 short
    entry: Literal["maker", "taker"]
    stop: float
    target: float
    limit: float | None = None  # maker entries
    ttl: int = 3  # maker: bars the limit rests
    max_hold: int = 30  # bars after the fill
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SimTrade:
    side: int
    entry_kind: str
    exit_kind: str  # target / stop / time
    entry_ms: int
    exit_ms: int
    entry: float
    exit: float
    qty: float
    fill_frac: float
    fees: float
    funding: float
    gross: float
    net: float
    risk: float
    r: float
    reason: str


@dataclass
class SimResult:
    trades: list[SimTrade] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    orders: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    adverse: dict[str, float] = field(default_factory=dict)
    kill_switch_ms: int | None = None


def _bump(d: dict[str, int], k: str, n: int = 1) -> None:
    d[k] = d.get(k, 0) + n


def simulate(
    series: BarSeries,
    setups: Sequence[Setup],
    scenario: Scenario,
    *,
    start: int = 0,
    end: int | None = None,
    capital: float = 10_000.0,
    risk_per_trade: float = 0.005,
    max_notional: float = 1.0,
    min_target_cost_mult: float = 3.0,
    funding: Mapping[int, float] | None = None,
    controller: ControllerLimits | None = None,
    kill_switch: float = 0.10,
    strategy: str = "s",
    adverse_horizon: int = 5,
) -> SimResult:
    """Replay setups whose signal bar lies in ``[start, end)``. One position
    at a time; a setup arriving while an order or a position is open is
    skipped (and counted)."""
    end = len(series) if end is None else min(end, len(series))
    o, h, lo, c, v, ts = (
        series.open,
        series.high,
        series.low,
        series.close,
        series.volume,
        series.ts,
    )
    sc = scenario
    ctrl = PortfolioController(capital, controller or ControllerLimits())
    res = SimResult()
    equity, peak = capital, capital
    fund = sorted((funding or {}).items())
    fund_ts = np.asarray([t for t, _ in fund], dtype=np.int64)
    fund_rate = np.asarray([r for _, r in fund], dtype=np.float64)
    moves_filled: list[float] = []
    moves_all: list[float] = []
    busy_until = -1
    sym = series.instrument.symbol
    for st in setups:
        if st.i < start or st.i >= end - 1:
            continue
        if st.i <= busy_until:
            _bump(res.skipped, "order or position already open")
            continue
        ref = float(c[st.i])
        entry_ref = st.limit if st.entry == "maker" and st.limit is not None else ref
        rt = sc.round_trip(st.entry)
        if abs(st.target - entry_ref) < min_target_cost_mult * rt * entry_ref:
            _bump(res.skipped, "target smaller than the cost multiple")
            continue
        stop_dist = abs(entry_ref - st.stop)
        if stop_dist <= 0 or st.side * (st.target - entry_ref) <= 0:
            _bump(res.skipped, "invalid stop or target")
            continue
        qty = equity * risk_per_trade / (stop_dist + rt * entry_ref)
        qty = min(qty, equity * max_notional / entry_ref)
        side_name = "long" if st.side > 0 else "short"
        veto = ctrl.check_entry(strategy, sym, side_name, qty * entry_ref, int(ts[st.i]))
        if veto:
            _bump(res.skipped, "controller: " + veto[0].split(" (")[0])
            continue
        # ---------------------------------------------------------------- entry
        fill_i, fill_px, frac, fee_rate = -1, 0.0, 0.0, 0.0
        _bump(res.orders, f"{st.entry}_placed")
        if st.entry == "taker":
            k = st.i + 1 + sc.latency_bars
            if k >= end:
                _bump(res.orders, "taker_unfilled")
                continue
            fill_i, frac, fee_rate = k, 1.0, sc.taker_fee
            fill_px = float(o[k]) * (1 + st.side * (sc.spread_bps / 2 + sc.taker_slip_bps) / 10_000)
        else:
            assert st.limit is not None
            lim = st.limit
            for k in range(st.i + 1, min(st.i + 1 + st.ttl, end)):
                # how far price went THROUGH the limit in this bar (bps)
                pen = ((lim - lo[k]) if st.side > 0 else (h[k] - lim)) / lim * 10_000
                if pen < sc.through_bps:
                    continue
                band = sc.partial_band_bps
                frac = 1.0 if band <= 0 else min(1.0, (pen - sc.through_bps) / band)
                if frac <= 0:
                    continue
                fill_i, fee_rate = k, sc.maker_fee
                fill_px = lim * (1 + st.side * sc.adverse_bps / 10_000)
                break
            # adverse selection: the move after placement, filled vs all orders
            hz = min(st.i + 1 + st.ttl + adverse_horizon, end - 1)
            move_all = st.side * (float(c[hz]) / lim - 1) * 10_000
            moves_all.append(move_all)
            if fill_i < 0:
                _bump(res.orders, "maker_unfilled")
                busy_until = st.i + st.ttl
                continue
            hz_f = min(fill_i + adverse_horizon, end - 1)
            moves_filled.append(st.side * (float(c[hz_f]) / lim - 1) * 10_000)
        _bump(res.orders, f"{st.entry}_filled")
        if frac < 1.0:
            _bump(res.orders, f"{st.entry}_partial")
        cap = sc.max_participation * float(v[fill_i])
        q = min(qty * frac, cap, equity * max_notional / fill_px)  # 1x at the FILL price
        if q <= 0:
            _bump(res.orders, f"{st.entry}_no_volume")
            busy_until = fill_i
            continue
        entry_fee = q * fill_px * fee_rate
        ctrl.on_open(strategy, sym, side_name, q * fill_px, int(ts[fill_i]))
        # ----------------------------------------------------------------- exit
        exit_kind, exit_i, exit_px, exit_fee_rate = "time", -1, 0.0, sc.taker_fee
        last = min(fill_i + st.max_hold, end - 1)
        for k in range(fill_i, last + 1):
            hit_stop = (lo[k] <= st.stop) if st.side > 0 else (h[k] >= st.stop)
            if hit_stop:
                gap = (o[k] <= st.stop) if st.side > 0 else (o[k] >= st.stop)
                base = float(o[k]) if gap and k > fill_i else st.stop
                exit_px = base * (1 - st.side * (sc.spread_bps / 2 + sc.taker_slip_bps) / 10_000)
                exit_kind, exit_i = "stop", k
                break
            if k > fill_i:
                pen = (
                    ((h[k] - st.target) if st.side > 0 else (st.target - lo[k]))
                    / st.target
                    * 10_000
                )
                if pen >= sc.through_bps:
                    exit_kind, exit_i, exit_px, exit_fee_rate = "target", k, st.target, sc.maker_fee
                    break
        if exit_i < 0:
            k = min(last + 1, end - 1)
            exit_i = k
            exit_px = float(o[k]) * (1 - st.side * (sc.spread_bps / 2 + sc.taker_slip_bps) / 10_000)
        exit_fee = q * exit_px * exit_fee_rate
        fund_cost = 0.0
        if len(fund_ts):
            a = int(np.searchsorted(fund_ts, int(ts[fill_i]), side="right"))
            b = int(np.searchsorted(fund_ts, int(ts[exit_i]), side="right"))
            fund_cost = float(st.side * q * fill_px * fund_rate[a:b].sum())
        gross = st.side * (exit_px - fill_px) * q
        net = gross - entry_fee - exit_fee - fund_cost
        risk = q * (abs(fill_px - st.stop) + rt * fill_px)
        trade = SimTrade(
            st.side,
            st.entry,
            exit_kind,
            int(ts[fill_i]),
            int(ts[exit_i]),
            fill_px,
            exit_px,
            q,
            frac,
            entry_fee + exit_fee,
            fund_cost,
            gross,
            net,
            risk,
            net / risk if risk > 0 else 0.0,
            st.reason,
        )
        res.trades.append(trade)
        ctrl.on_close(strategy, sym, net, int(ts[exit_i]))
        equity += net
        res.equity.append(equity)
        peak = max(peak, equity)
        if res.kill_switch_ms is None and equity <= peak * (1 - kill_switch):
            res.kill_switch_ms = int(ts[exit_i])
        busy_until = exit_i
    if moves_all:
        res.adverse = {
            "all_orders_bps": float(np.mean(moves_all)),
            "filled_bps": float(np.mean(moves_filled)) if moves_filled else math.nan,
            "horizon_bars": float(adverse_horizon),
        }
    return res


def stats(
    res: SimResult, capital: float = 10_000.0, *, bootstrap: int = 2000, seed: int = 7
) -> dict[str, object]:
    """Profit factor, average R, drawdown, trade count, and their uncertainty
    (bootstrap 95 % intervals over trades; one-sided p for mean R <= 0)."""
    t = res.trades
    n = len(t)
    nets = np.asarray([x.net for x in t], dtype=np.float64)
    rs = np.asarray([x.r for x in t], dtype=np.float64)
    eq = np.asarray([capital, *res.equity], dtype=np.float64)
    peak = np.maximum.accumulate(eq)
    dd = float(np.max((peak - eq) / peak)) if len(eq) else 0.0

    def pf(x: np.ndarray) -> float:
        loss = -x[x < 0].sum()
        return float(x[x > 0].sum() / loss) if loss > 0 else (99.0 if (x > 0).any() else 0.0)

    out: dict[str, object] = {
        "trades": n,
        "profit_factor": pf(nets),
        "avg_r": float(rs.mean()) if n else 0.0,
        "hit_rate": float((nets > 0).mean()) if n else 0.0,
        "net_pnl": float(nets.sum()),
        "return_pct": float(eq[-1] / capital - 1),
        "max_drawdown": dd,
        "fees": float(sum(x.fees for x in t)),
        "funding": float(sum(x.funding for x in t)),
        "orders": dict(res.orders),
        "skipped": dict(res.skipped),
        "adverse": dict(res.adverse),
        "kill_switch_ms": res.kill_switch_ms,
        "exits": {k: sum(1 for x in t if x.exit_kind == k) for k in ("target", "stop", "time")},
    }
    if n >= 2:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, n, size=(bootstrap, n))
        mean_r = rs[idx].mean(axis=1)
        pfs = np.asarray([pf(nets[j]) for j in idx[:500]])
        out["avg_r_ci95"] = [float(np.percentile(mean_r, 2.5)), float(np.percentile(mean_r, 97.5))]
        out["pf_ci95"] = [float(np.percentile(pfs, 2.5)), float(np.percentile(pfs, 97.5))]
        centred = rs - rs.mean()
        boot = centred[idx].mean(axis=1)
        out["p_value"] = (
            float((np.sum(boot >= rs.mean()) + 1) / (bootstrap + 1)) if rs.mean() > 0 else 1.0
        )
    else:
        out["avg_r_ci95"], out["pf_ci95"], out["p_value"] = None, None, 1.0
    return out


__all__ = [
    "BASE",
    "HARSH",
    "OPTIMISTIC",
    "SCENARIOS",
    "Scenario",
    "Setup",
    "SimResult",
    "SimTrade",
    "simulate",
    "stats",
]
