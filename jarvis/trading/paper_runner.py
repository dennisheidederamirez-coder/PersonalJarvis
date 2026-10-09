"""The forward paper-trading runner — manual, step by step, simulation only.

``step()`` processes every bar that CLOSED since the last step, for every
pre-registered stream, in time order, through the same engine, risk manager
and paper broker as the backtests. Nothing here talks to a network or an
exchange: bars come from a provider (``jarvis/market_data/paper_feed.py``
reads the local cache), orders go only to the in-memory paper broker.

Safeguards:
- **spec lock** — the spec's hash must match the expected hash and the hash
  this journal was started with; otherwise the runner refuses to run;
- **incomplete bars** — only bars whose close time has passed are used;
- **stale or disputed data** — a stream whose provider reports a problem,
  or whose newest closed bar is older than ``max_data_age_bars``, is not
  advanced (no new decisions), and the reason is journaled;
- **exactly once** — each stream remembers the last bar it processed; a
  missed period is caught up bar by bar, never twice;
- **atomic steps** — the journal entries of one bar and the resulting
  account state are committed in ONE transaction, so a crash in between
  leaves the previous consistent state and the bar is simply redone;
- **duplicates** — order ids are deterministic and remembered by the risk
  manager across restarts.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jarvis.trading.data import BarSeries, assess
from jarvis.trading.engine import DemoTrader
from jarvis.trading.journal import BufferJournal, SqliteJournal
from jarvis.trading.leverage import LeverageGrant
from jarvis.trading.metrics import max_drawdown
from jarvis.trading.paper import CostModel, PaperBroker
from jarvis.trading.paper_spec import INSTRUMENTS, PaperSpec, SpecError, StreamSpec
from jarvis.trading.risk import RiskLimits, RiskManager
from jarvis.trading.strategies import DonchianBreakout, SmaCross, Strategy


@dataclass
class StreamData:
    series: BarSeries | None
    ok: bool = True
    reason: str = ""
    funding: dict[int, float] | None = None  # observed funding of the same venue


Provider = Callable[[StreamSpec, int], StreamData]


@dataclass
class StepReport:
    at_ms: int
    processed: dict[str, int] = field(default_factory=dict)  # stream -> bars processed
    skipped: dict[str, str] = field(default_factory=dict)  # stream -> why


def _strategy(stream: StreamSpec) -> Strategy:
    params = dict(stream.params)
    if stream.strategy == "donchian_breakout":
        return DonchianBreakout(name=stream.id, **params)
    return SmaCross(name=stream.id, **params)


class PaperRunner:
    def __init__(
        self,
        spec: PaperSpec,
        journal: SqliteJournal,
        provider: Provider,
        *,
        now_ms: Callable[[], int],
        expected_digest: str | None = None,
        execution_fault: Callable[[str], str | None] | None = None,
        _fault_before_commit: Callable[[int], bool] | None = None,
    ) -> None:
        spec.validate()
        digest = spec.digest()
        if expected_digest is not None and expected_digest != digest:
            raise SpecError("the specification differs from the pre-registered hash")
        started = journal.load("spec_digest")
        if started is not None and started != digest:
            raise SpecError("this journal belongs to a different specification; start a new test")
        self.spec, self.journal, self.provider, self.now_ms = spec, journal, provider, now_ms
        self.digest = digest
        self._fault = _fault_before_commit
        # The paper test admits exactly the spec's markets (an explicit, hashed gate).
        self.instruments = {
            s: dataclasses.replace(i, demo_tradable=True) for s, i in INSTRUMENTS.items()
        }
        self.accounts: dict[str, DemoTrader] = {}
        for cand in spec.candidates():
            streams = [s for s in spec.streams if s.candidate == cand]
            costs = {
                s.symbol: CostModel(
                    fee_rate=spec.fee_rate, slippage_bps=s.slippage_bps, spread_bps=s.spread_bps
                )
                for s in streams
            }
            broker = PaperBroker(
                spec.capital_per_candidate, CostModel(fee_rate=spec.fee_rate), symbol_costs=costs
            )
            limits = RiskLimits(
                risk_per_trade=spec.risk_per_trade,
                max_open_risk=spec.max_open_risk,
                daily_loss_limit=spec.daily_loss_limit,
                max_drawdown=spec.max_drawdown,
                max_gross_exposure=spec.max_gross_exposure,
                max_positions=spec.max_positions,
                fee_rate=spec.fee_rate,
            )
            trader = DemoTrader(
                broker,
                RiskManager(limits),
                BufferJournal(),
                leverage={s.id: LeverageGrant(spec.leverage) for s in streams},
                max_volume_share=spec.max_volume_share,
                execution_fault=execution_fault,
                explain_no_signal=True,
            )
            saved = journal.load(f"account:{cand}")
            if saved is not None:
                trader.restore(saved, self.instruments)
            self.accounts[cand] = trader
        self.last_bar: dict[str, int] = journal.load("last_bar") or {}
        self.data_status: dict[str, str] = journal.load("data_status") or {}

    # -------------------------------------------------------------------- step

    def step(self) -> StepReport:
        now = self.now_ms()
        report = StepReport(now)
        waiting: dict[str, str] = {}
        events: list[tuple[int, str, StreamSpec, BarSeries, int, Strategy]] = []
        for stream in self.spec.streams:
            data = self.provider(stream, now)
            series = data.series
            if not data.ok or series is None or len(series) == 0:
                report.skipped[stream.id] = data.reason or "no data"
                continue
            if data.funding:
                self.accounts[stream.candidate].funding_by_symbol[stream.symbol] = data.funding
            if series.interval_ms != stream.interval_ms:
                report.skipped[stream.id] = "interval mismatch"
                continue
            closed = [
                k for k in range(len(series)) if int(series.ts[k]) + series.interval_ms <= now
            ]
            if not closed:
                report.skipped[stream.id] = "no closed bar yet"
                continue
            newest_close = int(series.ts[closed[-1]]) + series.interval_ms
            if now - newest_close > self.spec.max_data_age_bars * series.interval_ms:
                report.skipped[stream.id] = "data stale"
                continue
            if assess(series.slice(0, closed[-1] + 1)).grade == "unusable":
                report.skipped[stream.id] = "data unusable"
                continue
            last = self.last_bar.get(stream.id)
            new = [k for k in closed if last is None or int(series.ts[k]) > last]
            if last is not None and not new:
                waiting[stream.id] = (
                    f"no new closed bar since {_iso(last)}; next close "
                    f"{_iso(last + 2 * series.interval_ms)}"
                )
            if last is None:
                # first step: history is only indicator warm-up; start at the newest bar
                new = new[-1:]
            strategy = _strategy(stream)
            strategy.prepare(series.slice(0, closed[-1] + 1))
            for k in new:
                events.append(
                    (int(series.ts[k]) + series.interval_ms, stream.id, stream, series, k, strategy)
                )
        events.sort(key=lambda e: (e[0], e[1]))
        for n, (_close, sid, stream, series, k, strategy) in enumerate(events):
            trader = self.accounts[stream.candidate]
            buffer = BufferJournal()
            trader.journal = buffer
            trader.process_bar(series, k, strategy)
            buffer.record(
                "bar",
                int(series.ts[k]),
                stream.symbol,
                {
                    "stream": sid,
                    "close": float(series.close[k]),
                    "source": series.source,
                    "equity": trader.equity(),
                },
            )
            if self._fault is not None and self._fault(n):
                raise RuntimeError("simulated crash before commit")
            self.last_bar[sid] = int(series.ts[k])
            self.journal.commit(
                buffer.entries,
                {
                    f"account:{stream.candidate}": trader.snapshot(),
                    "last_bar": self.last_bar,
                    "spec_digest": self.digest,
                },
            )
            report.processed[sid] = report.processed.get(sid, 0) + 1
        self.data_status = {s.id: report.skipped.get(s.id, "ok") for s in self.spec.streams}
        notes: list[tuple[str, int, str, dict[str, Any]]] = []
        for stream in self.spec.streams:
            if stream.id in report.skipped:
                notes.append(
                    (
                        "no_signal",
                        now,
                        stream.symbol,
                        {"stream": stream.id, "reason": f"data: {report.skipped[stream.id]}"},
                    )
                )
            elif stream.id in waiting:
                notes.append(
                    (
                        "waiting",
                        now,
                        stream.symbol,
                        {"stream": stream.id, "reason": waiting[stream.id]},
                    )
                )
        self.journal.commit(
            [
                *notes,
                (
                    "step",
                    now,
                    "*",
                    {"processed": report.processed, "skipped": report.skipped, "waiting": waiting},
                ),
            ],
            {"data_status": self.data_status, "spec_digest": self.digest},
        )
        return report

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        trades = [d for _ts, _k, _s, d in self.journal.read("trade")]
        out: dict[str, Any] = {
            "spec": self.spec.name,
            "digest": self.digest[:16],
            "candidates": {},
            "data": dict(self.data_status),
        }
        for cand, trader in self.accounts.items():
            names = {s.id for s in self.spec.streams if s.candidate == cand}
            mine = [t for t in trades if t.get("strategy") in names]
            curve = [
                d["equity"]
                for _ts, _k, _s, d in self.journal.read("bar")
                if d.get("stream") in names
            ]
            st = trader.risk.state
            equity = trader.equity()
            peak = max([self.spec.capital_per_candidate, st.peak_equity, *curve])
            import numpy as np

            out["candidates"][cand] = {
                "equity": round(equity, 2),
                "cash": round(trader.broker.cash, 2),
                "return_pct": round(equity / self.spec.capital_per_candidate - 1, 4),
                "drawdown_now": round(1 - equity / peak, 4) if peak else 0.0,
                "max_drawdown": round(
                    max_drawdown(
                        np.asarray([self.spec.capital_per_candidate, *curve], dtype=float)
                    ),
                    4,
                ),
                "kill_switch": st.kill_switch,
                "halted_day": st.halted_day,
                "open_positions": [
                    {
                        "symbol": s,
                        "side": p.side.value,
                        "qty": p.qty,
                        "entry": round(p.entry, 4),
                        "stop": round(p.stop, 4),
                        "take_profit": p.take_profit,
                        "unrealized": round(p.unrealized(trader.marks.get(s, p.entry)), 2),
                    }
                    for s, p in trader.broker.positions.items()
                ],
                "trades": len(mine),
                "evaluable": len(mine) >= self.spec.min_trades_to_evaluate,
                "by_strategy": _stats(mine),
                "last_bar": {
                    sid: _iso(self.last_bar[sid]) for sid in sorted(names) if sid in self.last_bar
                },
            }
        return out

    def status_text(self) -> str:
        s = self.status()
        lines = [f"Paper test {s['spec']} (spec {s['digest']}) — simulation only"]
        for cand, c in s["candidates"].items():
            lines.append(
                f"[{cand}] equity {c['equity']:.2f} ({c['return_pct']:+.2%}), drawdown "
                f"{c['drawdown_now']:.2%} (max {c['max_drawdown']:.2%}), trades {c['trades']}"
                f"{'' if c['evaluable'] else ' — not yet evaluable'}"
                f"{' — KILL SWITCH ON' if c['kill_switch'] else ''}"
            )
            for p in c["open_positions"]:
                lines.append(
                    f"    open {p['side']} {p['symbol']} qty {p['qty']} entry {p['entry']}"
                    f" stop {p['stop']} unrealized {p['unrealized']:+.2f}"
                )
            for name, st in c["by_strategy"].items():
                lines.append(
                    f"    {name}: {st['trades']} trades, PF {st['profit_factor']:.2f}, "
                    f"avg R {st['avg_r']:+.2f}, net {st['net_pnl']:+.2f}, "
                    f"fees {st['fees']:.2f}"
                )
        bad = {k: v for k, v in s["data"].items() if v != "ok"}
        if bad:
            lines.append("data: " + ", ".join(f"{k} {v}" for k, v in sorted(bad.items())))
        return "\n".join(lines)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="minutes")


def _stats(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    from jarvis.trading.metrics import profit_factor

    out: dict[str, dict[str, Any]] = {}
    for name in sorted({t["strategy"] for t in trades}):
        ts = [t for t in trades if t["strategy"] == name]
        nets = [float(t["net"]) for t in ts]
        out[name] = {
            "trades": len(ts),
            "profit_factor": profit_factor(nets),
            "avg_r": sum(float(t["r_multiple"]) for t in ts) / len(ts),
            "net_pnl": sum(nets),
            "fees": sum(float(t["fees"]) for t in ts),
        }
    return out


__all__ = ["PaperRunner", "Provider", "StepReport", "StreamData"]
