"""The independent risk manager. Nothing opens a position without its approval.

It knows the account and the limits, never the strategy. It sizes every
entry so that a stop-out loses at most ``risk_per_trade`` of equity, refuses
entries that break a limit, and stops trading on its own:

- daily loss limit hit -> no new entries until the next UTC day;
- maximum drawdown from the equity peak hit -> kill switch;
- the kill switch (also manual) blocks every new entry and asks the engine to
  close all positions; only an explicit reset lifts it.

Closing a position is always allowed — it only reduces risk — except a
duplicate of an order already seen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from jarvis.trading.instruments import Instrument
from jarvis.trading.leverage import (
    LeverageGrant,
    initial_margin,
    leverage_reasons,
    liquidation_price,
    stop_before_liquidation,
)
from jarvis.trading.strategies import Side, stop_on_correct_side

RESET_PHRASE: Final = "reset kill switch"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade: float = 0.005  # 0.5 % of equity lost at the stop
    max_open_risk: float = 0.015  # sum of all open stop risks
    daily_loss_limit: float = 0.02  # vs. equity at the start of the UTC day
    max_drawdown: float = 0.10  # vs. the equity peak -> kill switch
    max_gross_exposure: float = 1.0  # sum of position notionals / equity (no leverage)
    max_positions: int = 3
    max_price_deviation: float = 0.02  # order reference vs. last mark
    min_stop_distance: float = 0.002
    max_stop_distance: float = 0.20
    min_notional: float = 10.0
    max_margin_usage: float = 0.5  # sum of isolated margins / equity
    liquidation_buffer: float = 0.5  # the stop uses at most half the way to liquidation
    max_correlated_risk: float = 0.01  # same-direction risk of correlated positions
    correlation_threshold: float = 0.7
    fee_rate: float = 0.0005  # for the liquidation fee reserve (match the cost model)

    def __post_init__(self) -> None:
        for name in ("risk_per_trade", "max_open_risk", "daily_loss_limit", "max_drawdown"):
            value = getattr(self, name)
            if not 0 < value < 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.risk_per_trade > self.max_open_risk:
            raise ValueError("risk_per_trade cannot exceed max_open_risk")


@dataclass(frozen=True, slots=True)
class OpenPosition:
    instrument: str
    side: Side
    qty: float
    entry: float
    stop: float
    mark: float
    margin: float = 0.0  # isolated margin tied up (notional / leverage)

    @property
    def notional(self) -> float:
        return abs(self.qty * self.mark)

    @property
    def risk(self) -> float:
        """What is lost if the stop is hit from the current mark (>= 0)."""
        return max(0.0, self.side.sign * (self.mark - self.stop) * self.qty)


@dataclass(frozen=True, slots=True)
class Account:
    equity: float
    positions: Sequence[OpenPosition] = ()
    marks: Mapping[str, float] = field(default_factory=dict)
    #: return correlations by symbol pair; a pair that is missing counts as
    #: fully correlated (unknown risk is treated as the worse case)
    correlations: Mapping[frozenset[str], float] = field(default_factory=dict)

    def correlation(self, a: str, b: str) -> float:
        return 1.0 if a == b else self.correlations.get(frozenset((a, b)), 1.0)


@dataclass(frozen=True, slots=True)
class EntryRequest:
    client_id: str  # deterministic per decision: a replay is a duplicate
    instrument: Instrument
    side: Side
    reference_price: float  # the price the decision was made at
    stop: float
    take_profit: float | None = None
    leverage: LeverageGrant = field(default_factory=LeverageGrant)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    qty: float = 0.0
    reasons: tuple[str, ...] = ()
    risk_amount: float = 0.0
    margin: float = 0.0
    liquidation: float | None = None


@dataclass
class RiskState:
    peak_equity: float = 0.0
    day: str = ""
    day_start_equity: float = 0.0
    halted_day: str | None = None
    kill_switch: bool = False
    kill_reason: str = ""
    seen_ids: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_equity": self.peak_equity,
            "day": self.day,
            "day_start_equity": self.day_start_equity,
            "halted_day": self.halted_day,
            "kill_switch": self.kill_switch,
            "kill_reason": self.kill_reason,
            "seen_ids": sorted(self.seen_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RiskState:
        return cls(
            float(data.get("peak_equity", 0.0)),
            str(data.get("day", "")),
            float(data.get("day_start_equity", 0.0)),
            data.get("halted_day"),
            bool(data.get("kill_switch", False)),
            str(data.get("kill_reason", "")),
            set(data.get("seen_ids", ())),
        )


def _utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).date().isoformat()


class RiskManager:
    def __init__(
        self,
        limits: RiskLimits | None = None,
        state: RiskState | None = None,
        *,
        research: bool = False,
    ) -> None:
        self.limits = limits or RiskLimits()
        self.state = state or RiskState()
        #: historical simulation only: instruments still in their analysis
        #: phase may be SIMULATED; every other limit applies unchanged. A
        #: live demo account never sets this.
        self.research = research

    # ----------------------------------------------------------- monitoring

    def observe(self, ts_ms: int, equity: float) -> list[str]:
        """Feed the marked-to-market equity after every bar; returns the risk
        events it raised (``daily_halt``, ``kill_switch``)."""
        events: list[str] = []
        st, lim = self.state, self.limits
        day = _utc_day(ts_ms)
        if day != st.day:
            st.day, st.day_start_equity = day, equity
        st.peak_equity = max(st.peak_equity, equity)
        if st.day_start_equity > 0 and st.halted_day != day:
            if equity <= st.day_start_equity * (1 - lim.daily_loss_limit):
                st.halted_day = day
                events.append("daily_halt")
        if not st.kill_switch and st.peak_equity > 0:
            if equity <= st.peak_equity * (1 - lim.max_drawdown):
                self.kill(f"drawdown {1 - equity / st.peak_equity:.1%} hit the limit")
                events.append("kill_switch")
        return events

    def kill(self, reason: str) -> None:
        self.state.kill_switch = True
        self.state.kill_reason = reason or "manual"

    def reset_kill(self, confirmation: str) -> bool:
        """Lift the kill switch — only with the exact confirmation phrase."""
        if confirmation.strip().lower() != RESET_PHRASE:
            return False
        self.state.kill_switch = False
        self.state.kill_reason = ""
        return True

    # ------------------------------------------------------------- sizing

    def size(self, equity: float, entry: float, stop: float, instrument: Instrument) -> float:
        distance = abs(entry - stop)
        if equity <= 0 or distance <= 0 or not math.isfinite(distance):
            return 0.0
        by_risk = equity * self.limits.risk_per_trade / distance
        by_exposure = equity * self.limits.max_gross_exposure / entry
        return instrument.round_qty(min(by_risk, by_exposure))

    # ------------------------------------------------------------- checks

    def check_entry(self, req: EntryRequest, account: Account) -> RiskDecision:
        lim, st = self.limits, self.state
        reasons: list[str] = []
        price, stop = req.reference_price, req.stop
        if req.client_id in st.seen_ids:
            return RiskDecision(False, reasons=("duplicate order",))
        if st.kill_switch:
            reasons.append(f"kill switch on: {st.kill_reason}")
        if st.halted_day is not None and st.halted_day == st.day:
            reasons.append("daily loss limit reached")
        if not req.instrument.demo_tradable and not self.research:
            reasons.append(f"{req.instrument.asset_class} is analysis-only in this phase")
        if req.side is Side.SHORT and not req.instrument.shortable:
            reasons.append("instrument cannot be shorted")
        values = (price, stop, account.equity) + (
            (req.take_profit,) if req.take_profit is not None else ()
        )
        if not all(math.isfinite(v) for v in values) or price <= 0 or stop <= 0:
            reasons.append("invalid price")
            return self._reject(req, reasons)
        if not stop_on_correct_side(req.side, price, stop):
            reasons.append("stop on the wrong side of the entry")
        if req.take_profit is not None and not stop_on_correct_side(
            req.side, req.take_profit, price
        ):
            reasons.append("take-profit on the wrong side of the entry")
        distance = abs(price - stop) / price
        if distance < lim.min_stop_distance:
            reasons.append("stop too close (noise would trigger it)")
        if distance > lim.max_stop_distance:
            reasons.append("stop too far")
        mark = account.marks.get(req.instrument.symbol)
        if mark is not None and abs(price / mark - 1) > lim.max_price_deviation:
            reasons.append("reference price far from the market")
        if any(p.instrument == req.instrument.symbol for p in account.positions):
            reasons.append("a position in this instrument is already open")
        if len(account.positions) >= lim.max_positions:
            reasons.append("maximum number of positions reached")
        reasons.extend(leverage_reasons(req.leverage))
        if not 0 < req.instrument.mmr < 1 / max(1.0, req.leverage.leverage):
            reasons.append("maintenance margin unknown or too high for this leverage")
        if reasons:
            return self._reject(req, reasons)

        qty = self.size(account.equity, price, stop, req.instrument)
        notional = qty * price
        risk_amount = qty * abs(price - stop)
        open_risk = sum(p.risk for p in account.positions)
        gross = sum(p.notional for p in account.positions)
        if qty <= 0 or notional < lim.min_notional:
            reasons.append("position too small")
        if open_risk + risk_amount > account.equity * lim.max_open_risk * (1 + 1e-9):
            reasons.append("total open risk would exceed the limit")
        if gross + notional > account.equity * lim.max_gross_exposure * (1 + 1e-9):
            reasons.append("gross exposure would exceed the limit")
        if reasons:
            return self._reject(req, reasons)
        lev = req.leverage.leverage
        margin = initial_margin(price, qty, lev)
        liq = liquidation_price(
            req.side, price, qty, lev, mmr=req.instrument.mmr, fee_rate=lim.fee_rate
        )
        if not stop_before_liquidation(req.side, price, stop, liq, buffer=lim.liquidation_buffer):
            reasons.append("stop not safely before the liquidation price")
        used = sum(p.margin or p.notional for p in account.positions)
        if used + margin > account.equity * lim.max_margin_usage * (1 + 1e-9) and lev > 1:
            reasons.append("margin usage would exceed the limit")
        correlated = risk_amount + sum(
            p.risk
            for p in account.positions
            if p.side is req.side
            and account.correlation(p.instrument, req.instrument.symbol)
            >= lim.correlation_threshold
        )
        if correlated > account.equity * lim.max_correlated_risk * (1 + 1e-9):
            reasons.append("correlated risk in this direction would exceed the limit")
        if reasons:
            return self._reject(req, reasons)
        st.seen_ids.add(req.client_id)
        return RiskDecision(True, qty, (), risk_amount, margin, liq)

    def check_exit(self, client_id: str) -> RiskDecision:
        if client_id in self.state.seen_ids:
            return RiskDecision(False, reasons=("duplicate order",))
        self.state.seen_ids.add(client_id)
        return RiskDecision(True)

    def _reject(self, req: EntryRequest, reasons: list[str]) -> RiskDecision:
        # A rejected id is remembered too: the same decision replayed after a
        # restart must not slip through on a second try.
        self.state.seen_ids.add(req.client_id)
        return RiskDecision(False, reasons=tuple(reasons))


__all__ = [
    "RESET_PHRASE",
    "Account",
    "EntryRequest",
    "OpenPosition",
    "RiskDecision",
    "RiskLimits",
    "RiskManager",
    "RiskState",
]
