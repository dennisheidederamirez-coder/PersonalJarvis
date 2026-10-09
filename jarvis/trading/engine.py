"""One bar loop for backtests and the live demo — the same execution rules.

Per bar ``i`` of an instrument:

1. **open** — execute what was decided at the previous close (exits first,
   then entries). An entry whose stop the market already gapped through is
   cancelled; one whose risk grew by more than half is cancelled.
2. **intrabar** — stop and take-profit against the bar's range; a gap fills
   at the open; if both levels are inside one bar the STOP is assumed first
   (the conservative reading of an unknown path).
3. **close** — funding, mark-to-market, risk observation (daily halt, kill
   switch), then the strategy's target, checked by the risk manager and
   queued for the next open. Decisions use bar ``i``'s close and execute at
   bar ``i+1``'s open: no decision ever trades at a price it could not know.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from jarvis.trading.data import HOUR_MS, BarSeries, DataQuality, require_usable
from jarvis.trading.journal import Journal, MemoryJournal
from jarvis.trading.leverage import LeverageGrant
from jarvis.trading.metrics import by_strategy, compute
from jarvis.trading.paper import CostModel, PaperBroker, Trade
from jarvis.trading.risk import Account, EntryRequest, OpenPosition, RiskLimits, RiskManager
from jarvis.trading.strategies import Strategy, Target

MAX_RISK_GROWTH = 1.5  # an entry is cancelled if its stop risk grew beyond this at the open

Candidate = tuple[Strategy, Target]
#: Orders the entry candidates of one bar (best first) and drops the ones that
#: may not trade; see ``setups.StrategyBook.rank``. Without one, candidates
#: are tried in the order the strategies were given.
Ranker = Callable[[BarSeries, int, list[Candidate]], list[Candidate]]


@dataclass
class _Pending:
    close_reason: str | None = None
    close_id: str = ""
    entry: EntryRequest | None = None
    qty: float = 0.0
    planned_risk: float = 0.0
    reason: str = ""
    strategy: str = ""


@dataclass
class DemoTrader:
    broker: PaperBroker
    risk: RiskManager
    journal: Journal = field(default_factory=MemoryJournal)
    trades: list[Trade] = field(default_factory=list)
    marks: dict[str, float] = field(default_factory=dict)
    on_trade: Callable[[Trade], None] | None = None
    #: fixed leverage per strategy name (default 1x); never changed at runtime
    leverage: dict[str, LeverageGrant] = field(default_factory=dict)
    _pending: dict[str, _Pending] = field(default_factory=dict)

    # ------------------------------------------------------------------ api

    def account(self, *, excluding: str | None = None) -> Account:
        positions = [
            OpenPosition(s, p.side, p.qty, p.entry, p.stop, self.marks.get(s, p.entry), p.margin)
            for s, p in self.broker.positions.items()
            if s != excluding
        ]
        return Account(self.broker.equity(self.marks), positions, dict(self.marks))

    def equity(self) -> float:
        return self.broker.equity(self.marks)

    def process_bar(
        self,
        series: BarSeries,
        i: int,
        strategies: Strategy | Sequence[Strategy],
        *,
        decide: bool = True,
        rank: Ranker | None = None,
    ) -> None:
        """One bar of one instrument. With several strategies, an open position
        is managed only by the strategy that opened it; while flat, every
        strategy proposes and the ranked candidates go to the risk manager in
        order until one is approved — no strategy needs another's consent."""
        sym = series.instrument.symbol
        ts = int(series.ts[i])
        o, h, lo, c = (
            float(series.open[i]),
            float(series.high[i]),
            float(series.low[i]),
            float(series.close[i]),
        )
        self._execute_pending(series, ts, o)
        self._check_exits(sym, ts, o, h, lo)
        self.marks[sym] = c
        self.broker.accrue_funding(sym, c, series.interval_ms / HOUR_MS)
        for event in self.risk.observe(ts, self.equity()):
            self.journal.record(
                "risk_event",
                ts,
                sym,
                {
                    "event": event,
                    "equity": self.equity(),
                    "reason": self.risk.state.kill_reason,
                },
            )
        if self.risk.state.kill_switch:
            self._queue_flatten(ts, "kill switch")
            return
        if not decide:
            return
        books = [strategies] if not isinstance(strategies, Sequence) else list(strategies)
        pos = self.broker.positions.get(sym)
        if pos is not None:
            owner = next((s for s in books if s.name == pos.strategy), None)
            if owner is None:  # owner no longer active: stop / target still protect it
                return
            target = owner.decide(i, pos.side)
            if target is not None:
                self._handle_target(series, ts, c, owner, target)
            return
        candidates: list[Candidate] = []
        for strategy in books:
            target = strategy.decide(i, None)
            if target is not None and target.side is not None:
                candidates.append((strategy, target))
        ordered = rank(series, i, candidates) if rank is not None else candidates
        for strategy, target in ordered:
            if self._handle_target(series, ts, c, strategy, target):
                break

    def close_all(self, ts: int, reason: str) -> None:
        for sym in list(self.broker.positions):
            self._close(
                sym,
                self.marks.get(sym, self.broker.positions[sym].entry),
                ts,
                reason,
                f"close:{sym}:{ts}:{reason}",
            )

    # ------------------------------------------------------------ internals

    def _queue_flatten(self, ts: int, reason: str) -> None:
        for sym in self.broker.positions:
            pending = self._pending.setdefault(sym, _Pending())
            pending.close_reason, pending.close_id = reason, f"flatten:{sym}:{ts}"
            pending.entry = None

    def _handle_target(
        self, series: BarSeries, ts: int, close: float, strategy: Strategy, target: Target
    ) -> bool:
        """Queue the exit and/or entry; True when something was queued."""
        sym = series.instrument.symbol
        pos = self.broker.positions.get(sym)
        side = target.side
        decision_id = f"{strategy.name}:{sym}:{ts}:{side.value if side else 'flat'}"
        self.journal.record(
            "decision",
            ts,
            sym,
            {
                "strategy": strategy.name,
                "target": side.value if side else "flat",
                "stop": target.stop,
                "take_profit": target.take_profit,
                "reason": target.reason,
                "price": close,
            },
        )
        pending = _Pending()
        if pos is not None and pos.side is not side:
            pending.close_reason = target.reason or "strategy exit"
            pending.close_id = f"exit:{decision_id}"
        if side is not None and target.stop is not None and (pos is None or pos.side is not side):
            req = EntryRequest(
                decision_id,
                series.instrument,
                side,
                close,
                target.stop,
                target.take_profit,
                self.leverage.get(strategy.name, LeverageGrant()),
            )
            verdict = self.risk.check_entry(req, self.account(excluding=sym))
            if verdict.approved:
                pending.entry, pending.qty = req, verdict.qty
                pending.planned_risk, pending.reason = verdict.risk_amount, target.reason
                pending.strategy = strategy.name
                self.journal.record(
                    "approved",
                    ts,
                    sym,
                    {
                        "client_id": req.client_id,
                        "qty": verdict.qty,
                        "risk": verdict.risk_amount,
                        "side": side.value,
                    },
                )
            else:
                self.journal.record(
                    "rejected",
                    ts,
                    sym,
                    {
                        "client_id": req.client_id,
                        "side": side.value,
                        "reasons": list(verdict.reasons),
                    },
                )
        if pending.close_reason or pending.entry:
            self._pending[sym] = pending
            return True
        return False

    def _execute_pending(self, series: BarSeries, ts: int, open_: float) -> None:
        sym = series.instrument.symbol
        pending = self._pending.pop(sym, None)
        if pending is None:
            return
        if pending.close_reason and sym in self.broker.positions:
            self._close(sym, open_, ts, pending.close_reason, pending.close_id)
        req = pending.entry
        if req is None or self.risk.state.kill_switch:
            return
        gapped = req.side.sign * (open_ - req.stop) <= 0
        risk_now = pending.qty * abs(open_ - req.stop)
        if gapped or risk_now > pending.planned_risk * MAX_RISK_GROWTH:
            self.journal.record(
                "cancelled",
                ts,
                sym,
                {
                    "client_id": req.client_id,
                    "reason": "gapped through the stop" if gapped else "risk grew at the open",
                },
            )
            return
        fill = self.broker.open(
            req.client_id,
            req.instrument,
            req.side,
            pending.qty,
            open_,
            req.stop,
            req.take_profit,
            ts,
            pending.reason,
            strategy=pending.strategy,
            leverage=req.leverage.leverage,
            mmr=req.instrument.mmr,
        )
        self.journal.record("fill", ts, sym, _fill_dict(fill))

    def _check_exits(self, sym: str, ts: int, o: float, h: float, lo: float) -> None:
        pos = self.broker.positions.get(sym)
        if pos is None:
            return
        s = pos.side.sign
        liq = pos.liquidation
        if liq is not None and ((o <= liq) if s > 0 else (o >= liq)):
            # Gapped through the liquidation price: the isolated margin is lost.
            # Without a gap the stop — which always lies before the liquidation
            # price — is reached first on any continuous path.
            self._close(sym, liq, ts, "liquidation", f"liq:{pos.client_id}")
            return
        stop_hit = (lo <= pos.stop) if s > 0 else (h >= pos.stop)
        tp = pos.take_profit
        tp_hit = tp is not None and ((h >= tp) if s > 0 else (lo <= tp))
        if stop_hit:
            gap = (o <= pos.stop) if s > 0 else (o >= pos.stop)
            self._close(sym, o if gap else pos.stop, ts, "stop", f"stop:{pos.client_id}")
        elif tp_hit and tp is not None:
            gap = (o >= tp) if s > 0 else (o <= tp)
            self._close(sym, o if gap else tp, ts, "take_profit", f"tp:{pos.client_id}")

    def _close(self, sym: str, price: float, ts: int, reason: str, client_id: str) -> None:
        if not self.risk.check_exit(client_id).approved:
            self.journal.record(
                "rejected", ts, sym, {"client_id": client_id, "reasons": ["duplicate order"]}
            )
            return
        fill, trade = self.broker.close(sym, price, ts, reason, client_id)
        self.trades.append(trade)
        self.journal.record("fill", ts, sym, _fill_dict(fill))
        self.journal.record("trade", ts, sym, trade.to_dict())
        if self.on_trade is not None:
            self.on_trade(trade)


def _fill_dict(fill: Any) -> dict[str, Any]:
    return {
        f: (getattr(fill, f).value if f == "side" else getattr(fill, f))
        for f in fill.__dataclass_fields__
    }


# ------------------------------------------------------------------ backtest


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    params: dict[str, Any]
    symbol: str
    start_ms: int
    end_ms: int
    trades: tuple[Trade, ...]
    equity: NDArray[np.float64]
    metrics: dict[str, Any]
    quality: DataQuality
    rejections: int
    kill_switch: bool


def run_backtest(
    series: BarSeries,
    strategy: Strategy | Sequence[Strategy],
    *,
    rank: Ranker | None = None,
    capital: float = 10_000.0,
    costs: CostModel | None = None,
    limits: RiskLimits | None = None,
    start: int = 0,
    end: int | None = None,
    journal: Journal | None = None,
    leverage: Mapping[str, LeverageGrant] | None = None,
) -> BacktestResult:
    """Replay *series*; trade only in ``[start, end)``. Bars before ``start``
    serve as indicator history (warm-up), never as tradable bars. Several
    strategies share one account; ``metrics["by_strategy"]`` keeps each one's
    trades apart."""
    quality = require_usable(series)
    end = len(series) if end is None else min(end, len(series))
    if not 0 <= start < end:
        raise ValueError("empty backtest window")
    books = [strategy] if not isinstance(strategy, Sequence) else list(strategy)
    if len({s.name for s in books}) != len(books):
        raise ValueError("strategy names must be unique (they attribute trades)")
    for s in books:
        s.prepare(series)
    journal = journal or MemoryJournal()
    cost_model = costs or CostModel()
    risk_limits = limits or RiskLimits(fee_rate=cost_model.fee_rate)
    trader = DemoTrader(
        PaperBroker(capital, cost_model),
        RiskManager(risk_limits),
        journal,
        leverage=dict(leverage or {}),
    )
    curve: list[float] = []
    for i in range(start, end):
        trader.process_bar(series, i, books, decide=i < end - 1, rank=rank)
        curve.append(trader.equity())
    last_ts = int(series.ts[end - 1])
    trader.close_all(last_ts, "end of test")
    if curve:
        curve[-1] = trader.equity()
    equity = np.asarray([capital, *curve], dtype=np.float64)
    rejections = sum(1 for e in getattr(journal, "entries", []) if e[0] == "rejected")
    metrics = compute(
        trader.trades, equity, capital=capital, periods_per_year=series.periods_per_year
    )
    metrics["by_strategy"] = by_strategy(trader.trades)
    return BacktestResult(
        "+".join(s.name for s in books),
        dict(books[0].params) if len(books) == 1 else {s.name: dict(s.params) for s in books},
        series.instrument.symbol,
        int(series.ts[start]),
        last_ts,
        tuple(trader.trades),
        equity,
        metrics,
        quality,
        rejections,
        trader.risk.state.kill_switch,
    )


__all__ = ["BacktestResult", "DemoTrader", "run_backtest"]
