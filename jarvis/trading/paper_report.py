"""The daily paper-trading report — built only from the journal and the
account state, for one UTC day. Nothing is sent anywhere; the caller stores
or prints it."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from jarvis.trading.metrics import profit_factor
from jarvis.trading.paper_runner import PaperRunner

_REASON_KINDS = ("no_signal", "waiting", "hold", "rejected", "cancelled", "execution_failed")


def _day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


def daily_report(runner: PaperRunner, day: date) -> dict[str, Any]:
    lo, hi = _day_bounds(day)
    rows = runner.journal.read()
    status = runner.status()
    out: dict[str, Any] = {
        "day": day.isoformat(),
        "spec": runner.spec.name,
        "digest": runner.digest[:16],
        "candidates": {},
        "data": dict(runner.data_status),
    }
    for cand, trader in runner.accounts.items():
        streams = {s.id: s for s in runner.spec.streams if s.candidate == cand}
        symbols = {s.symbol for s in streams.values()}
        trades = [d for ts, k, _s, d in rows if k == "trade" and d.get("strategy") in streams]
        today = [t for t in trades if lo <= int(t["exit_ms"]) < hi]
        fills_today = [d for ts, k, s, d in rows if k == "fill" and lo <= ts < hi and s in symbols]
        open_pos = []
        unrealized = 0.0
        for sym, pos in trader.broker.positions.items():
            mark = trader.marks.get(sym, pos.entry)
            u = pos.unrealized(mark)
            unrealized += u
            open_pos.append(
                {
                    "symbol": sym,
                    "strategy": pos.strategy,
                    "side": pos.side.value,
                    "qty": pos.qty,
                    "entry": pos.entry,
                    "mark": mark,
                    "stop": pos.stop,
                    "unrealized": round(u, 2),
                    "funding_paid": round(pos.funding, 4),
                    "risk_at_stop": round(abs(mark - pos.stop) * pos.qty, 2),
                }
            )
        reasons: dict[str, Counter[str]] = {}
        for ts, kind, _sym, d in rows:
            if kind in _REASON_KINDS and lo <= ts < hi:
                key = d.get("stream") or d.get("strategy") or d.get("client_id", "?")
                reason = d.get("reason") or "; ".join(d.get("reasons", [])) or kind
                if key in streams or any(
                    key.startswith(f"{sid}:") or sid in key for sid in streams
                ):
                    reasons.setdefault(key, Counter())[f"{kind}: {reason}"] += 1
        c = status["candidates"][cand]
        out["candidates"][cand] = {
            "equity": c["equity"],
            "cash": c["cash"],
            "return_pct": c["return_pct"],
            "drawdown_now": c["drawdown_now"],
            "max_drawdown": c["max_drawdown"],
            "kill_switch": c["kill_switch"],
            "halted_day": c["halted_day"],
            "realized_today": round(sum(float(t["net"]) for t in today), 2),
            "realized_total": round(sum(float(t["net"]) for t in trades), 2),
            "unrealized": round(unrealized, 2),
            "fees_today": round(sum(float(f["fee"]) for f in fills_today), 2),
            "fees_total": round(sum(float(t["fees"]) for t in trades), 2),
            "funding_total": round(
                sum(float(t["funding"]) for t in trades)
                + sum(float(p["funding_paid"]) for p in open_pos),
                4,
            ),
            "trades_today": len(today),
            "trades_total": len(trades),
            "profit_factor": profit_factor([float(t["net"]) for t in trades]),
            "evaluable": c["evaluable"],
            "open_positions": open_pos,
            "not_traded_because": {k: dict(v) for k, v in sorted(reasons.items())},
        }
    return out


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Paper test {report['spec']} — {report['day']} (simulation only)",
        f"spec {report['digest']}",
        "",
    ]
    for cand, c in report["candidates"].items():
        lines += [
            f"## Candidate {cand}",
            f"- equity {c['equity']:.2f} ({c['return_pct']:+.2%}), cash {c['cash']:.2f}",
            f"- realized today {c['realized_today']:+.2f}, total {c['realized_total']:+.2f}; "
            f"unrealized {c['unrealized']:+.2f}",
            f"- drawdown now {c['drawdown_now']:.2%}, max {c['max_drawdown']:.2%}"
            f"{'; KILL SWITCH ON' if c['kill_switch'] else ''}",
            f"- costs: fees today {c['fees_today']:.2f}, fees total {c['fees_total']:.2f}, "
            f"funding total {c['funding_total']:+.4f}",
            f"- trades today {c['trades_today']}, total {c['trades_total']}, profit factor "
            f"{c['profit_factor']:.2f}{'' if c['evaluable'] else ' (not yet evaluable)'}",
        ]
        for p in c["open_positions"]:
            lines.append(
                f"- open {p['side']} {p['symbol']} ({p['strategy']}) qty {p['qty']} entry "
                f"{p['entry']:.6g} mark {p['mark']:.6g} unrealized {p['unrealized']:+.2f}"
                f" risk at stop {p['risk_at_stop']:.2f}"
            )
        if c["not_traded_because"]:
            lines.append("- not traded because:")
            for key, reasons in c["not_traded_because"].items():
                for reason, n in reasons.items():
                    lines.append(f"  - {key}: {reason}" + (f" (x{n})" if n > 1 else ""))
        lines.append("")
    lines.append("## Data")
    lines += [f"- {k}: {v}" for k, v in sorted(report["data"].items())]
    return "\n".join(lines) + "\n"


__all__ = ["daily_report", "to_markdown"]
