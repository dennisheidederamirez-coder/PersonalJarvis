"""The dashboard's sections, built from one journal snapshot.

Every function here is pure: a snapshot (or a read failure), the
pre-registered spec and the current time in, a wire model out. A failure
produces the same model with empty data and ``source.state`` saying why, so
the UI renders one message instead of an error page.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import numpy as np

from jarvis.trading.metrics import max_drawdown, profit_factor
from jarvis.trading.paper_spec import PaperSpec
from jarvis.trading.timeframes import LABEL
from jarvis.trading_dashboard import schema as s
from jarvis.trading_dashboard.reader import JournalRow, JournalSnapshot, ReadFailure
from jarvis.trading_dashboard.reasons import classify

# The paper job's schedule and budgets (``jarvis.market_data.paper_job``),
# copied so the dashboard never imports the job's network adapters;
# ``test_views.py`` pins them to the job's own constants.
SLOT_HOURS: Final = (0, 4, 8, 12, 16, 20)
SLOT_DELAY_MIN: Final = 5
MAX_REQUESTS_PER_RUN: Final = 40
MAX_REQUESTS_PER_DAY: Final = 300
#: A run later than its slot by this much counts as overdue.
RUN_GRACE_MS: Final = 30 * 60_000

_SLOT_MS: Final = 4 * 3_600_000
_DAY_MS: Final = 86_400_000

SIGNAL_KINDS: Final = ("decision", "approved", "rejected")
EXECUTION_KINDS: Final = ("fill", "trade", "cancelled", "execution_failed")
NO_TRADE_KINDS: Final = ("no_signal", "hold", "waiting")
RISK_KINDS: Final = ("risk_event",)
GROUPS: Final = {
    "signal": SIGNAL_KINDS,
    "execution": EXECUTION_KINDS,
    "no_trade": NO_TRADE_KINDS,
    "risk": RISK_KINDS,
}
_GROUP_OF: Final = {k: g for g, kinds in GROUPS.items() for k in kinds}
_JOB_INCIDENTS: Final = ("job_refused", "job_expired", "job_disabled")
_CLIENT_PREFIXES: Final = ("stop:", "tp:", "liq:", "exit:", "flatten:")
#: Engine kinds decided at a bar's close but journaled under its open time.
_AT_CLOSE: Final = frozenset({"decision", "approved", "rejected", "no_signal", "hold"})
_RISK_EVENTS: Final = {"kill_switch": "kill_switch", "daily_halt": "daily_loss_limit"}


# --------------------------------------------------------------------- context


@dataclass
class DashboardContext:
    """Everything derived once per request from one snapshot."""

    spec: PaperSpec
    now_ms: int
    snap: JournalSnapshot | None
    failure: ReadFailure | None
    stream_of: dict[int, str | None] = field(default_factory=dict)
    intervals: dict[str, int] = field(default_factory=dict)
    signals: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def state(self) -> dict[str, Any]:
        return self.snap.state if self.snap else {}

    @property
    def rows(self) -> tuple[JournalRow, ...]:
        return self.snap.rows if self.snap else ()

    def kinds(self, *kinds: str) -> list[JournalRow]:
        return [r for r in self.rows if r.kind in kinds]

    def candidate_of(self, stream: str | None) -> str | None:
        if not stream:
            return None
        cand = stream.split(":", 1)[0]
        return cand if cand in self.spec.candidates() else None


def context(
    result: JournalSnapshot | ReadFailure, spec: PaperSpec, now_ms: int
) -> DashboardContext:
    if isinstance(result, ReadFailure):
        return DashboardContext(spec, now_ms, None, result)
    ctx = DashboardContext(spec, now_ms, result, None)
    ctx.stream_of = _attribute(result.rows, set(spec.candidates()))
    ctx.intervals = {st.id: st.interval_ms for st in spec.streams}
    for r in result.rows:
        if r.kind == "decision":
            key = f"{r.data.get('strategy')}:{r.symbol}:{r.ts_ms}:{r.data.get('target')}"
            text = str(r.data.get("reason") or "")
            ctx.signals[key] = (classify("decision", text), text)
    return ctx


def _stream_from_fields(data: dict[str, Any], candidates: set[str]) -> str | None:
    for key in ("stream", "strategy"):
        value = data.get(key)
        if isinstance(value, str) and value.split(":", 1)[0] in candidates and ":" in value:
            return value
    cid = data.get("client_id")
    if isinstance(cid, str):
        for prefix in _CLIENT_PREFIXES:
            if cid.startswith(prefix):
                cid = cid[len(prefix) :]
                break
        parts = cid.split(":")
        if len(parts) >= 2 and parts[0] in candidates:
            return f"{parts[0]}:{parts[1]}"
    return None


def _attribute(rows: Iterable[JournalRow], candidates: set[str]) -> dict[int, str | None]:
    """Stream of every row. Rows without a stream field (a kill-switch note, a
    risk event, a flatten order) belong to the bar committed with them: the
    runner commits a bar's entries followed by its ``bar`` row."""
    out: dict[int, str | None] = {}
    following: dict[str, str] = {}
    for r in sorted(rows, key=lambda x: x.id, reverse=True):
        if r.kind == "step" or r.symbol == "*":
            following.clear()
            out[r.id] = None
            continue
        own = _stream_from_fields(r.data, candidates)
        if r.kind == "bar" and own:
            following[r.symbol] = own
        out[r.id] = own or following.get(r.symbol)
    return out


def source(ctx: DashboardContext) -> s.Source:
    if ctx.failure is not None:
        f = ctx.failure
        return s.Source(
            state=f.state.value,
            detail=f.detail,
            file_name=_name(f.path),
            read_at_ms=ctx.now_ms,
        )
    snap = ctx.snap
    if snap is None:  # pragma: no cover - a context has a snapshot or a failure
        raise ValueError("context without a snapshot")
    warnings: list[str] = []
    test = _test(ctx)
    if not test.spec_matches and ctx.state.get("spec_digest") is not None:
        warnings.append("spec_mismatch")
    if test.state == "running" and _run_overdue(ctx):
        warnings.append("run_overdue")
    if test.runs_missed > 0:
        warnings.append("runs_missed")
    if test.state == "refused":
        warnings.append("job_refused")
    data_status = ctx.state.get("data_status") or {}
    if any(v != "ok" for v in data_status.values()):
        warnings.append("data_problem")
    if any(
        (ctx.state.get(f"account:{c}") or {}).get("risk", {}).get("kill_switch")
        for c in ctx.spec.candidates()
    ):
        warnings.append("kill_switch")
    if snap.truncated:
        warnings.append("rows_truncated")
    if snap.bad_rows:
        warnings.append("bad_rows")
    return s.Source(
        state="ok",
        detail="",
        file_name=_name(snap.path),
        read_at_ms=ctx.now_ms,
        journal_mtime_ms=snap.mtime_ms,
        size_bytes=snap.size_bytes,
        read_ms=snap.read_ms,
        truncated=snap.truncated,
        bad_rows=snap.bad_rows,
        warnings=warnings,
    )


def _name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


# -------------------------------------------------------------------- schedule


def slot_times(start_ms: int, end_ms: int) -> list[int]:
    """Scheduled run times t with start < t <= end."""
    if end_ms <= start_ms:
        return []
    out: list[int] = []
    day = datetime.fromtimestamp(start_ms / 1000, UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    while int(day.timestamp() * 1000) <= end_ms:
        for hour in SLOT_HOURS:
            t = int((day + timedelta(hours=hour, minutes=SLOT_DELAY_MIN)).timestamp() * 1000)
            if start_ms < t <= end_ms:
                out.append(t)
        day += timedelta(days=1)
    return out


def next_slot(after_ms: int) -> int:
    return slot_times(after_ms, after_ms + _DAY_MS + _SLOT_MS)[0]


def _last_run(ctx: DashboardContext) -> int | None:
    value = ctx.state.get("last_job_run")
    if isinstance(value, (int, float)):
        return int(value)
    runs = ctx.kinds("job_run")
    return runs[-1].ts_ms if runs else None


def _run_overdue(ctx: DashboardContext) -> bool:
    job = ctx.state.get("job") or {}
    start = int(job.get("from_ms") or 0)
    end = min(ctx.now_ms, int(job.get("until_ms") or ctx.now_ms))
    due = [t for t in slot_times(start, end) if t + RUN_GRACE_MS <= ctx.now_ms]
    if not due:
        return False
    last = _last_run(ctx)
    return last is None or last < due[-1]


# ------------------------------------------------------------------- the test


def _test(ctx: DashboardContext) -> s.PaperTestStatus:
    spec = ctx.spec
    digest = spec.digest()
    journal_digest = ctx.state.get("spec_digest")
    job = ctx.state.get("job") or {}
    out = s.PaperTestStatus(
        spec_name=spec.name,
        spec_digest=str(journal_digest or digest)[:16],
        spec_matches=journal_digest is None or journal_digest == digest,
        state="not_enabled",
        code_digest=str(job.get("code") or "")[:16],
    )
    from_ms = job.get("from_ms")
    until_ms = job.get("until_ms")
    if from_ms is None or until_ms is None:
        return out
    start, end = int(from_ms), int(until_ms)
    out.from_ms, out.until_ms = start, end
    out.days_total = max(1, round((end - start) / _DAY_MS))
    elapsed = max(0, min(ctx.now_ms, end) - start)
    out.progress = round(min(1.0, elapsed / (end - start)), 4) if end > start else 1.0
    out.day = min(out.days_total, elapsed // _DAY_MS + 1)
    runs = [r for r in ctx.kinds("job_run") if start <= r.ts_ms <= end]
    out.runs_done = len(runs)
    passed = slot_times(start, min(ctx.now_ms, end))
    out.runs_expected = len(passed)
    # a slot counts as missed only once its grace period is over
    due = [t for t in passed if t + RUN_GRACE_MS <= ctx.now_ms]
    out.runs_missed = max(0, len(due) - out.runs_done)
    out.last_run_ms = _last_run(ctx)
    refusals = [r for r in ctx.kinds("job_refused") if r.ts_ms >= start]
    out.refusals = len(refusals)
    last_ok = runs[-1].ts_ms if runs else start
    if not job.get("enabled"):
        if ctx.kinds("job_expired") or ctx.now_ms >= end:
            out.state = "finished"
        else:
            out.state = "disabled"
    elif ctx.now_ms >= end:
        out.state = "finished"
    elif refusals and refusals[-1].ts_ms > last_ok:
        out.state = "refused"
    else:
        out.state = "running"
        out.next_run_ms = next_slot(ctx.now_ms)
    return out


# ------------------------------------------------------------------ accounts


def _account_state(ctx: DashboardContext, cand: str) -> dict[str, Any]:
    value = ctx.state.get(f"account:{cand}")
    return value if isinstance(value, dict) else {}


def _positions_raw(ctx: DashboardContext, cand: str) -> dict[str, dict[str, Any]]:
    broker = _account_state(ctx, cand).get("broker") or {}
    positions = broker.get("positions") or {}
    return positions if isinstance(positions, dict) else {}


def _unrealized(p: dict[str, Any], mark: float) -> float:
    sign = 1.0 if p.get("side") == "long" else -1.0
    return sign * (mark - float(p["entry"])) * float(p["qty"])


def _risk_at_stop(p: dict[str, Any], mark: float) -> float:
    sign = 1.0 if p.get("side") == "long" else -1.0
    return max(0.0, sign * (mark - float(p["stop"]))) * float(p["qty"])


def _trades(ctx: DashboardContext, cand: str | None = None) -> list[JournalRow]:
    rows = ctx.kinds("trade")
    if cand is None:
        return rows
    return [r for r in rows if ctx.candidate_of(_trade_stream(ctx, r)) == cand]


def _trade_stream(ctx: DashboardContext, r: JournalRow) -> str | None:
    strategy = r.data.get("strategy")
    if isinstance(strategy, str) and strategy:
        return strategy
    return ctx.stream_of.get(r.id)


def _equity_points(ctx: DashboardContext, cand: str) -> list[s.EquityPoint]:
    """The account's equity after each processed bar, at the bar's CLOSE."""
    intervals = {st.id: st.interval_ms for st in ctx.spec.streams}
    by_ts: dict[int, float] = {}
    for r in ctx.kinds("bar"):
        stream = ctx.stream_of.get(r.id)
        if stream and ctx.candidate_of(stream) == cand and "equity" in r.data:
            by_ts[r.ts_ms + intervals.get(stream, 0)] = float(r.data["equity"])
    return [s.EquityPoint(ts_ms=t, equity=round(v, 2)) for t, v in sorted(by_ts.items())]


def _accounts(ctx: DashboardContext) -> list[s.Account]:
    spec = ctx.spec
    out: list[s.Account] = []
    for cand in spec.candidates():
        acc = _account_state(ctx, cand)
        broker = acc.get("broker") or {}
        marks = acc.get("marks") or {}
        risk = acc.get("risk") or {}
        capital = float(broker.get("capital") or spec.capital_per_candidate)
        cash = float(broker.get("cash", capital))
        positions = _positions_raw(ctx, cand)
        unreal = sum(
            _unrealized(p, float(marks.get(sym, p["entry"]))) for sym, p in positions.items()
        )
        equity = cash + unreal
        trades = _trades(ctx, cand)
        nets = [float(t.data.get("net", 0.0)) for t in trades]
        curve = [p.equity for p in _equity_points(ctx, cand)]
        peak = max([capital, float(risk.get("peak_equity") or 0.0), *curve, equity])
        day_start = float(risk.get("day_start_equity") or capital)
        out.append(
            s.Account(
                candidate=cand,
                streams=[st.id for st in spec.streams if st.candidate == cand],
                capital=round(capital, 2),
                cash=round(cash, 2),
                equity=round(equity, 2),
                return_pct=round(equity / capital - 1, 6),
                realized=round(sum(nets), 2),
                unrealized=round(unreal, 2),
                fees=round(
                    sum(float(t.data.get("fees", 0.0)) for t in trades)
                    + sum(float(p.get("entry_fee", 0.0)) for p in positions.values()),
                    2,
                ),
                funding=round(
                    sum(float(t.data.get("funding", 0.0)) for t in trades)
                    + sum(float(p.get("funding", 0.0)) for p in positions.values()),
                    4,
                ),
                day_pnl=round(equity - day_start, 2),
                peak_equity=round(peak, 2),
                drawdown_now=round(1 - equity / peak, 6) if peak else 0.0,
                max_drawdown=round(
                    max_drawdown(np.asarray([capital, *curve, equity], dtype=float)),
                    6,
                ),
                kill_switch=bool(risk.get("kill_switch")),
                kill_reason=str(risk.get("kill_reason") or ""),
                halted_day=risk.get("halted_day"),
                trades=len(trades),
                open_positions=len(positions),
                evaluable=len(trades) >= spec.min_trades_to_evaluate,
                min_trades=spec.min_trades_to_evaluate,
            )
        )
    return out


def _all_positions(ctx: DashboardContext) -> list[s.Position]:
    out: list[s.Position] = []
    for cand in ctx.spec.candidates():
        marks = _account_state(ctx, cand).get("marks") or {}
        for sym, p in sorted(_positions_raw(ctx, cand).items()):
            mark = float(marks.get(sym, p["entry"]))
            out.append(
                s.Position(
                    candidate=cand,
                    stream=str(p.get("strategy") or "") or None,
                    symbol=sym,
                    side=str(p.get("side")),
                    qty=float(p["qty"]),
                    entry=float(p["entry"]),
                    mark=mark,
                    stop=float(p["stop"]),
                    take_profit=(
                        float(p["take_profit"]) if p.get("take_profit") is not None else None
                    ),
                    unrealized=round(_unrealized(p, mark), 2),
                    risk_at_stop=round(_risk_at_stop(p, mark), 2),
                    notional=round(abs(float(p["qty"]) * mark), 2),
                    funding=round(float(p.get("funding", 0.0)), 4),
                    opened_ms=int(p.get("opened_ms", 0)),
                    leverage=float(p.get("leverage", 1.0)),
                )
            )
    return out


def _overlap(positions: list[s.Position]) -> list[str]:
    counts = Counter(p.symbol for p in positions)
    return sorted(sym for sym, n in counts.items() if n > 1)


def overview(ctx: DashboardContext) -> s.Overview:
    accounts = _accounts(ctx) if ctx.snap else []
    positions = _all_positions(ctx) if ctx.snap else []
    capital = sum(a.capital for a in accounts)
    equity = sum(a.equity for a in accounts)
    return s.Overview(
        source=source(ctx),
        test=_test(ctx),
        accounts=accounts,
        combined=s.Combined(
            capital=round(capital, 2),
            equity=round(equity, 2),
            return_pct=round(equity / capital - 1, 6) if capital else 0.0,
            realized=round(sum(a.realized for a in accounts), 2),
            unrealized=round(sum(a.unrealized for a in accounts), 2),
            open_risk=round(sum(p.risk_at_stop for p in positions), 2),
            same_symbol_overlap=_overlap(positions),
        ),
    )


# ----------------------------------------------------------------- decisions


def _decision(ctx: DashboardContext, r: JournalRow) -> s.Decision:
    d = r.data
    stream = ctx.stream_of.get(r.id)
    if r.kind == "rejected":
        reasons = [str(x) for x in d.get("reasons") or []]
        text = "; ".join(reasons)
        code = classify(r.kind, reasons[0]) if reasons else "other"
    elif r.kind == "trade":
        text = str(d.get("exit_reason") or "")
        code = classify(r.kind, text)
    elif r.kind == "risk_event":
        event = str(d.get("event") or "")
        text = "; ".join(x for x in (event, str(d.get("reason") or "")) if x)
        code = _RISK_EVENTS.get(event) or classify(r.kind, text)
    elif r.kind == "approved":
        text, code = "", "approved"
    else:
        text = str(d.get("reason") or d.get("note") or "")
        code = classify(r.kind, text)
    signal_code = signal_reason = None
    cid = d.get("client_id")
    if isinstance(cid, str) and cid in ctx.signals:
        signal_code, signal_reason = ctx.signals[cid]
    side = d.get("side") or (d.get("target") if r.kind == "decision" else None)
    ts = r.ts_ms
    if r.kind in _AT_CLOSE and stream and ("strategy" in d or "client_id" in d):
        # the engine journals decisions under the bar's OPEN time; they are made at its close
        ts += ctx.intervals.get(stream, 0)
    return s.Decision(
        id=r.id,
        ts_ms=ts,
        kind=r.kind,
        group=_GROUP_OF.get(r.kind, "other"),
        symbol=r.symbol,
        stream=stream,
        candidate=ctx.candidate_of(stream),
        reason_code=code,
        reason=text,
        signal_code=signal_code,
        signal_reason=signal_reason,
        side=str(side) if side else None,
        qty=_f(d.get("qty")),
        price=_f(d.get("price") if "price" in d else d.get("exit")),
        stop=_f(d.get("stop")),
        take_profit=_f(d.get("take_profit")),
        risk=_f(d.get("risk")),
        net=_f(d.get("net")),
        r_multiple=_f(d.get("r_multiple")),
    )


def _f(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # a non-numeric journal field shows as '—', not as an error
        return None


def decisions(
    ctx: DashboardContext,
    *,
    group: str = "all",
    stream: str | None = None,
    reason_code: str | None = None,
    limit: int = 50,
    before_id: int | None = None,
    since_ms: int | None = None,
) -> s.DecisionPage:
    kinds = GROUPS.get(group) or tuple(k for ks in GROUPS.values() for k in ks)
    rows = [r for r in ctx.rows if r.kind in kinds]
    if since_ms is not None:
        rows = [r for r in rows if r.ts_ms >= since_ms]
    if stream:
        rows = [r for r in rows if ctx.stream_of.get(r.id) == stream]
    items = [_decision(ctx, r) for r in rows]
    counts = Counter((d.kind, d.reason_code) for d in items)
    if reason_code:
        items = [d for d in items if d.reason_code == reason_code or d.signal_code == reason_code]
    items.sort(key=lambda d: d.id, reverse=True)
    total = len(items)
    if before_id is not None:
        items = [d for d in items if d.id < before_id]
    page = items[: max(1, min(limit, 500))]
    more = len(items) > len(page)
    return s.DecisionPage(
        source=source(ctx),
        items=page,
        total=total,
        next_before_id=page[-1].id if more and page else None,
        reason_counts=[
            s.ReasonCount(kind=k, reason_code=c, count=n)
            for (k, c), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        streams=[st.id for st in ctx.spec.streams],
    )


# ---------------------------------------------------------------- strategies


def _latest_by_stream(ctx: DashboardContext, kinds: tuple[str, ...]) -> dict[str, JournalRow]:
    out: dict[str, JournalRow] = {}
    for r in ctx.rows:
        if r.kind in kinds:
            sid = ctx.stream_of.get(r.id)
            if sid:
                out[sid] = r
    return out


def strategies(ctx: DashboardContext) -> s.StrategiesView:
    if ctx.snap is None:
        return s.StrategiesView(source=source(ctx), streams=[])
    last_bar = ctx.state.get("last_bar") or {}
    data_status = ctx.state.get("data_status") or {}
    latest = _latest_by_stream(ctx, (*SIGNAL_KINDS, *EXECUTION_KINDS, *NO_TRADE_KINDS))
    trades = ctx.kinds("trade")
    out: list[s.StreamStatus] = []
    for st in ctx.spec.streams:
        acc = _account_state(ctx, st.candidate)
        pos = _positions_raw(ctx, st.candidate).get(st.symbol)
        mine = pos if pos and pos.get("strategy") == st.id else None
        pending = (acc.get("pending") or {}).get(st.symbol)
        ds = str(data_status.get(st.id, "unknown"))
        lb = last_bar.get(st.id)
        if (acc.get("risk") or {}).get("kill_switch"):
            status = "kill_switch"
        elif lb is None:
            status = "not_started"
        elif ds not in ("ok", "unknown"):
            status = "data_problem"
        elif mine is not None:
            status = "position_open"
        elif pending and pending.get("strategy") == st.id:
            status = "order_pending"
        elif st.id in latest and latest[st.id].kind == "waiting":
            status = "waiting"
        else:
            status = "no_signal"
        own_trades = [t for t in trades if _trade_stream(ctx, t) == st.id]
        out.append(
            s.StreamStatus(
                id=st.id,
                candidate=st.candidate,
                symbol=st.symbol,
                interval=LABEL.get(st.interval_ms, f"{st.interval_ms}ms"),
                strategy=st.strategy,
                params={
                    k: v for k, v in st.params.items() if isinstance(v, (int, float, bool, str))
                },
                status=status,
                data_status=ds,
                last_close_ms=int(lb) + st.interval_ms if lb is not None else None,
                next_close_ms=int(lb) + 2 * st.interval_ms if lb is not None else None,
                last_event=_decision(ctx, latest[st.id]) if st.id in latest else None,
                position_side=str(mine["side"]) if mine else None,
                trades=len(own_trades),
                net=round(sum(float(t.data.get("net", 0.0)) for t in own_trades), 2),
            )
        )
    return s.StrategiesView(source=source(ctx), streams=out)


# ------------------------------------------------------------ positions, risk


def positions(ctx: DashboardContext) -> s.PositionsView:
    if ctx.snap is None:
        return s.PositionsView(
            source=source(ctx),
            positions=[],
            pending=[],
            risk=[],
            same_symbol_overlap=[],
            open_risk=0.0,
            notional=0.0,
        )
    spec = ctx.spec
    pos = _all_positions(ctx)
    accounts = {a.candidate: a for a in _accounts(ctx)}
    pending: list[s.PendingOrder] = []
    risk: list[s.AccountRisk] = []
    for cand in spec.candidates():
        acc = _account_state(ctx, cand)
        for sym, p in sorted((acc.get("pending") or {}).items()):
            entry = p.get("entry")
            close = p.get("close_reason")
            action = "exit_and_entry" if close and entry else ("exit" if close else "entry")
            reason = str(close or p.get("reason") or "")
            pending.append(
                s.PendingOrder(
                    candidate=cand,
                    symbol=sym,
                    stream=str(p.get("strategy") or "") or None,
                    action=action,
                    side=str(entry["side"]) if entry else None,
                    qty=float(p.get("qty") or 0.0),
                    reason=reason,
                    reason_code=classify("pending", reason),
                )
            )
        a = accounts[cand]
        mine = [x for x in pos if x.candidate == cand]
        eq = a.equity if a.equity > 0 else 1.0
        open_risk = sum(x.risk_at_stop for x in mine)
        notional = sum(x.notional for x in mine)
        day_start = a.equity - a.day_pnl
        daily_loss = max(0.0, -a.day_pnl) / day_start if day_start > 0 else 0.0
        limits = [
            _use("open_risk", open_risk / eq, spec.max_open_risk),
            _use("gross_exposure", notional / eq, spec.max_gross_exposure),
            _use("positions", float(len(mine)), float(spec.max_positions)),
            _use("daily_loss", daily_loss, spec.daily_loss_limit),
            _use("drawdown", a.drawdown_now, spec.max_drawdown),
        ]
        risk.append(
            s.AccountRisk(
                candidate=cand,
                equity=a.equity,
                limits=limits,
                kill_switch=a.kill_switch,
                kill_reason=a.kill_reason,
                halted_day=a.halted_day,
            )
        )
    return s.PositionsView(
        source=source(ctx),
        positions=pos,
        pending=pending,
        risk=risk,
        same_symbol_overlap=_overlap(pos),
        open_risk=round(sum(x.risk_at_stop for x in pos), 2),
        notional=round(sum(x.notional for x in pos), 2),
    )


def _use(key: str, used: float, limit: float) -> s.LimitUse:
    return s.LimitUse(
        key=key,
        used=round(used, 6),
        limit=limit,
        ratio=round(used / limit, 4) if limit > 0 else 0.0,
    )


# --------------------------------------------------------------- performance


def _perf(key: str, cand: str, rows: list[JournalRow], **extra: str | None) -> s.PerfRow:
    nets = [float(r.data.get("net", 0.0)) for r in rows]
    rs = [float(r.data.get("r_multiple", 0.0)) for r in rows]
    holds = [
        (int(r.data["exit_ms"]) - int(r.data["entry_ms"])) / 3_600_000
        for r in rows
        if "exit_ms" in r.data and "entry_ms" in r.data
    ]
    wins = sum(1 for x in nets if x > 0)
    losses = sum(1 for x in nets if x < 0)
    pf = profit_factor(nets) if rows else None
    return s.PerfRow(
        key=key,
        candidate=cand,
        trades=len(rows),
        wins=wins,
        losses=losses,
        win_rate=round(wins / len(rows), 4) if rows else None,
        profit_factor=None if pf is None else round(pf, 4),
        avg_r=round(sum(rs) / len(rs), 4) if rs else None,
        net=round(sum(nets), 2),
        fees=round(sum(float(r.data.get("fees", 0.0)) for r in rows), 2),
        funding=round(sum(float(r.data.get("funding", 0.0)) for r in rows), 4),
        best=round(max(nets), 2) if nets else None,
        worst=round(min(nets), 2) if nets else None,
        avg_hold_h=round(sum(holds) / len(holds), 2) if holds else None,
        **extra,
    )


def performance(ctx: DashboardContext, *, recent: int = 100) -> s.PerformanceView:
    spec = ctx.spec
    criteria = s.Criteria(
        profit_factor_min=float(spec.success.get("profit_factor_min", 0.0)),
        avg_r_min=float(spec.success.get("avg_r_min_exclusive", 0.0)),
        max_drawdown_max=float(spec.success.get("max_drawdown_max", 0.0)),
        min_trades=spec.min_trades_to_evaluate,
    )
    if ctx.snap is None:
        return s.PerformanceView(
            source=source(ctx),
            by_stream=[],
            by_candidate=[],
            by_symbol=[],
            equity=[],
            criteria=criteria,
            trades=[],
        )
    trades = ctx.kinds("trade")
    by_stream = [
        _perf(
            st.id,
            st.candidate,
            [t for t in trades if _trade_stream(ctx, t) == st.id],
            symbol=st.symbol,
            strategy=st.strategy,
        )
        for st in spec.streams
    ]
    by_candidate = [_perf(c, c, _trades(ctx, c)) for c in spec.candidates()]
    symbols = sorted({st.symbol for st in spec.streams})
    by_symbol = [
        _perf(sym, "*", [t for t in trades if t.symbol == sym], symbol=sym) for sym in symbols
    ]
    equity = []
    job = ctx.state.get("job") or {}
    for cand in spec.candidates():
        points = _equity_points(ctx, cand)
        start = job.get("from_ms")
        if start is not None and (not points or points[0].ts_ms > int(start)):
            capital = float(
                (_account_state(ctx, cand).get("broker") or {}).get("capital")
                or spec.capital_per_candidate
            )
            points.insert(0, s.EquityPoint(ts_ms=int(start), equity=round(capital, 2)))
        equity.append(s.EquitySeries(candidate=cand, points=points))
    rows = []
    for t in sorted(trades, key=lambda r: r.id, reverse=True)[:recent]:
        d = t.data
        stream = _trade_stream(ctx, t)
        rows.append(
            s.TradeRow(
                id=t.id,
                candidate=ctx.candidate_of(stream),
                stream=stream,
                symbol=t.symbol,
                side=str(d.get("side", "")),
                entry_ms=int(d.get("entry_ms", 0)),
                exit_ms=int(d.get("exit_ms", t.ts_ms)),
                entry=float(d.get("entry", 0.0)),
                exit=float(d.get("exit", 0.0)),
                net=round(float(d.get("net", 0.0)), 2),
                fees=round(float(d.get("fees", 0.0)), 2),
                r_multiple=round(float(d.get("r_multiple", 0.0)), 4),
                exit_code=classify("trade", str(d.get("exit_reason") or "")),
                exit_reason=str(d.get("exit_reason") or ""),
            )
        )
    return s.PerformanceView(
        source=source(ctx),
        by_stream=by_stream,
        by_candidate=by_candidate,
        by_symbol=by_symbol,
        equity=equity,
        criteria=criteria,
        trades=rows,
    )


# -------------------------------------------------------------------- health


def health(ctx: DashboardContext, *, runs: int = 30, incidents: int = 50) -> s.HealthView:
    if ctx.snap is None:
        return s.HealthView(
            source=source(ctx),
            streams=[],
            runs=[],
            requests_today=0,
            request_budget_day=MAX_REQUESTS_PER_DAY,
            request_budget_run=MAX_REQUESTS_PER_RUN,
            incidents=[],
            run_overdue=False,
        )
    last_bar = ctx.state.get("last_bar") or {}
    data_status = ctx.state.get("data_status") or {}
    streams: list[s.StreamHealth] = []
    for st in ctx.spec.streams:
        iv = st.interval_ms
        expected = (ctx.now_ms // iv) * iv - iv  # open time of the newest closed bar
        lb = last_bar.get(st.id)
        lag = None if lb is None else max(0, (expected - int(lb)) // iv)
        ds = str(data_status.get(st.id, "unknown"))
        # a just-closed bar is processed at the next slot: one bar behind is
        # normal for up to one slot plus the grace period
        late = (
            lag is not None and lag >= 1 and ctx.now_ms - (expected + iv) > _SLOT_MS + RUN_GRACE_MS
        )
        streams.append(
            s.StreamHealth(
                id=st.id,
                symbol=st.symbol,
                interval=LABEL.get(iv, f"{iv}ms"),
                data_status=ds,
                data_code="ok" if ds == "ok" else classify("no_signal", f"data: {ds}"),
                last_close_ms=int(lb) + iv if lb is not None else None,
                expected_close_ms=expected + iv,
                lag_bars=lag,
                stale=bool(late or (lag is not None and lag > ctx.spec.max_data_age_bars)),
            )
        )
    job_runs = [
        s.JobRun(
            ts_ms=r.ts_ms,
            processed=sum(int(v) for v in (r.data.get("processed") or {}).values()),
            skipped={str(k): str(v) for k, v in (r.data.get("skipped") or {}).items()},
            requests=int(r.data.get("requests") or 0),
            notes=[str(n) for n in r.data.get("notes") or []],
        )
        for r in reversed(ctx.kinds("job_run")[-runs:])
    ]
    incident_rows = [
        r
        for r in ctx.rows
        if r.kind in (*_JOB_INCIDENTS, "risk_event", "execution_failed", "cancelled")
        or (r.kind == "no_signal" and str(r.data.get("reason", "")).startswith("data:"))
    ][-incidents:]
    found = [
        s.Incident(
            id=r.id,
            ts_ms=r.ts_ms,
            kind=r.kind,
            symbol=r.symbol,
            reason_code=_decision(ctx, r).reason_code if r.kind not in _JOB_INCIDENTS else r.kind,
            reason=str(r.data.get("reason") or r.data.get("event") or r.data.get("by") or ""),
        )
        for r in reversed(incident_rows)
    ]
    today = datetime.fromtimestamp(ctx.now_ms / 1000, UTC).date().isoformat()
    budget = ctx.state.get("request_budget") or {}
    test = _test(ctx)
    return s.HealthView(
        source=source(ctx),
        streams=streams,
        runs=job_runs,
        requests_today=int(budget.get(today, 0)),
        request_budget_day=MAX_REQUESTS_PER_DAY,
        request_budget_run=MAX_REQUESTS_PER_RUN,
        incidents=found,
        last_run_ms=test.last_run_ms,
        next_run_ms=test.next_run_ms,
        run_overdue=test.state == "running" and _run_overdue(ctx),
    )


# ------------------------------------------------------------------- reports


def reports(ctx: DashboardContext, *, limit: int = 60) -> s.ReportsView:
    out: list[s.DailyReport] = []
    by_day: dict[str, JournalRow] = {}
    for r in ctx.kinds("daily_report"):
        day = str(r.data.get("day") or "")
        if day:
            by_day[day] = r  # a re-written day keeps its newest report
    for day in sorted(by_day, reverse=True)[:limit]:
        d = by_day[day].data
        ops = d.get("operations") or {}
        comb = d.get("combined") or {}
        cands = []
        for cand, c in sorted((d.get("candidates") or {}).items()):
            cands.append(
                s.ReportCandidate(
                    candidate=str(cand),
                    equity=float(c.get("equity", 0.0)),
                    return_pct=float(c.get("return_pct", 0.0)),
                    realized_today=float(c.get("realized_today", 0.0)),
                    realized_total=float(c.get("realized_total", 0.0)),
                    unrealized=float(c.get("unrealized", 0.0)),
                    fees_today=float(c.get("fees_today", 0.0)),
                    trades_today=int(c.get("trades_today", 0)),
                    trades_total=int(c.get("trades_total", 0)),
                    drawdown_now=float(c.get("drawdown_now", 0.0)),
                    kill_switch=bool(c.get("kill_switch")),
                    open_positions=len(c.get("open_positions") or []),
                )
            )
        out.append(
            s.DailyReport(
                day=day,
                runs=int(ops.get("job_runs", 0)),
                expected_runs=int(ops.get("expected_runs", 0)),
                requests=int(ops.get("requests", 0)),
                notes=[str(n) for n in ops.get("notes") or []],
                refusals=[str(n) for n in ops.get("refusals") or []],
                candidates=cands,
                combined_equity=_f(comb.get("equity")),
                open_risk=_f(comb.get("open_risk_at_stops")),
                same_symbol_overlap=[str(x) for x in comb.get("same_symbol_overlap") or []],
                data={str(k): str(v) for k, v in (d.get("data") or {}).items()},
            )
        )
    return s.ReportsView(source=source(ctx), reports=out)


__all__ = [
    "GROUPS",
    "DashboardContext",
    "MAX_REQUESTS_PER_DAY",
    "MAX_REQUESTS_PER_RUN",
    "RUN_GRACE_MS",
    "SLOT_DELAY_MIN",
    "SLOT_HOURS",
    "context",
    "decisions",
    "health",
    "next_slot",
    "overview",
    "performance",
    "positions",
    "reports",
    "slot_times",
    "source",
    "strategies",
]
