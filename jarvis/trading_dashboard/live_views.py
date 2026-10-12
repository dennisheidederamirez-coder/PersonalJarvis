"""The LIVE history's wire models, computed from one archive snapshot.

Pure functions of ``(snapshot, now)``; a failed read gives empty sections and
a ``source.state`` that says why. Nothing here shares data with the paper
views: the snapshot comes from the LIVE archive only.

Definitions (stated once, used everywhere):

- **realized** is the venue's realized P/L, which EXCLUDES fees and funding;
  **net** = realized − fee + funding; funding is signed, negative = paid.
- Totals count CLOSED positions, dated by their close; an open position's
  fees and funding show in its own row until it closes.
- Days and months are UTC.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from jarvis.live_history.models import Fill, Position, is_derived
from jarvis.live_history.reconcile import (
    DEFAULT_TOLERANCES,
    Code,
    Finding,
    JournalEntry,
    Severity,
    check_balance,
    check_fill_totals,
    check_internal,
    compare_twin,
    reconcile_journal,
)
from jarvis.live_history.sync import Stream, plan_gaps
from jarvis.trading_dashboard import schema as s
from jarvis.trading_dashboard.live_reader import LiveSnapshot
from jarvis.trading_dashboard.reader import ReadFailure

DAY_MS: Final = 86_400_000
#: A successful sync older than this is called out (there is no automatic sync).
SYNC_OLD_MS: Final = 7 * DAY_MS
RECENT_MS: Final = 30 * DAY_MS
MAX_ISSUES_SHOWN: Final = 200
MAX_FINDINGS_SHOWN: Final = 500


@dataclass(frozen=True)
class _Hit:
    group: str  # internal / journal / balance / export / totals
    finding: Finding
    period: tuple[int, int] | None = None  # balance: the two observations


@dataclass
class _Recon:
    findings: list[_Hit]
    journal: str | None
    journal_span: tuple[int, int] | None
    pnl_basis: str | None
    positions_compared: int
    entries_compared: int
    matched: int
    balance_checks: int


@dataclass
class LiveContext:
    snap: LiveSnapshot | ReadFailure
    now_ms: int
    _recon: dict[str | None, _Recon] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return isinstance(self.snap, LiveSnapshot)

    def recon(self, journal: str | None = None) -> _Recon:
        snap = self.snap
        if not isinstance(snap, LiveSnapshot):
            return _Recon([], None, None, None, 0, 0, 0, 0)
        name = journal if journal in snap.journals else _default_journal(snap)
        if name not in self._recon:
            self._recon[name] = _reconcile(snap, name)
        return self._recon[name]


def context(snap: LiveSnapshot | ReadFailure, now_ms: int) -> LiveContext:
    return LiveContext(snap, now_ms)


# --------------------------------------------------------------- helpers


def _f(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


def _name(path: str) -> str:
    """The file name only — the dashboard never echoes a full path."""
    return Path(path).name


def _closed(snap: LiveSnapshot) -> list[Position]:
    return [p for p in snap.positions if p.status == "closed"]


def _origin(snap: LiveSnapshot, entity: str, key: str) -> str:
    return "csv" if snap.sources.get((entity, key), "").startswith("csv:") else "api"


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _totals(positions: Iterable[Position]) -> s.LiveTotals:
    ps = list(positions)
    nets = [p.net for p in ps]
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    return s.LiveTotals(
        realized=float(sum((p.realized_pnl for p in ps), Decimal(0))),
        fees=float(sum((p.fee for p in ps), Decimal(0))),
        funding=float(sum((p.funding for p in ps), Decimal(0))),
        net=float(sum(nets, Decimal(0))),
        closed=len(ps),
        wins=wins,
        losses=losses,
        win_rate=wins / (wins + losses) if wins + losses else None,
    )


def _run(r: Any) -> s.LiveRun:
    # "csv:positions:<digest>" → "csv:positions": the digest is shown with the import
    source = ":".join(str(r.source).split(":")[:2])
    return s.LiveRun(
        id=r.id,
        kind=r.kind,
        source=source,
        started_ms=r.started_ms,
        finished_ms=r.finished_ms,
        status=r.status,
        requests=r.requests,
        inserted=r.inserted,
        updated=r.updated,
        unchanged=r.unchanged,
        rejected=r.rejected,
        error_code=r.error_code,
    )


def _since(snap: LiveSnapshot) -> int | None:
    starts = [iv[0][0] for iv in snap.coverage.values() if iv]
    starts += [p.opened_ms for p in snap.positions[:1]] + [f.ts_ms for f in snap.fills[:1]]
    return min(starts) if starts else None


def _gaps(snap: LiveSnapshot, now_ms: int) -> list[s.LiveGap]:
    since = _since(snap)
    ends = [iv[-1][1] for iv in snap.coverage.values() if iv]
    if since is None or not ends:
        return []
    plan = plan_gaps(snap.coverage, since_ms=since, until_ms=max(ends), now_ms=now_ms)
    return [
        s.LiveGap(
            stream=w.stream.value, from_ms=w.from_ms, to_ms=w.to_ms, route=w.route, note=w.note
        )
        for w in plan
    ]


# ----------------------------------------------------------------- source


def source(ctx: LiveContext) -> s.LiveSource:
    snap = ctx.snap
    if isinstance(snap, ReadFailure):
        return s.LiveSource(
            state=snap.state.value,
            detail=snap.detail,
            file_name=_name(snap.path),
            read_at_ms=ctx.now_ms,
        )
    runs = snap.runs
    syncs = [r for r in runs if r.kind in ("sync", "backfill") and r.status == "ok"]
    imports = [r for r in runs if r.kind == "import"]
    last_sync = max((r.finished_ms or r.started_ms for r in syncs), default=None)
    warnings: list[str] = []
    if not syncs:
        warnings.append("no_sync_yet")
    elif last_sync is not None and ctx.now_ms - last_sync > SYNC_OLD_MS:
        warnings.append("sync_old")
    if runs and runs[0].status == "failed":
        warnings.append("last_run_failed")
    elif runs and runs[0].status == "partial":
        warnings.append("last_run_partial")
    if any(g.route == "api" and g.stream != Stream.ORDERS for g in _gaps(snap, ctx.now_ms)):
        warnings.append("coverage_gaps")
    if imports and imports[0].status != "ok":
        warnings.append("import_issues")
    if snap.truncated:
        warnings.append("records_truncated")
    if snap.origin == "unknown":
        warnings.append("origin_unknown")
    return s.LiveSource(
        state="ok",
        detail="",
        file_name=_name(snap.path),
        read_at_ms=ctx.now_ms,
        archive_mtime_ms=snap.mtime_ms,
        size_bytes=snap.size_bytes,
        read_ms=snap.read_ms,
        last_sync_ms=last_sync,
        last_import_ms=max((r.finished_ms or r.started_ms for r in imports), default=None),
        origin=snap.origin,
        warnings=warnings,
    )


# --------------------------------------------------------------- overview


def _balance_point(b: Any) -> s.LiveBalancePoint:
    return s.LiveBalancePoint(
        ts_ms=b.ts_ms,
        wallet=float(b.wallet),
        available=float(b.available),
        unrealized=float(b.cross_unrealized + b.isolation_unrealized),
    )


def overview(ctx: LiveContext) -> s.LiveOverview:
    src = source(ctx)
    snap = ctx.snap
    if not isinstance(snap, LiveSnapshot):
        empty = _totals([])
        return s.LiveOverview(
            source=src,
            balance_series=[],
            totals=empty,
            totals_30d=empty,
            pnl_series=[],
            open_positions=0,
            open_unrealized=0.0,
            fills=0,
            orders=0,
            findings={},
        )
    closed = _closed(snap)
    by_day: dict[str, Decimal] = defaultdict(Decimal)
    for p in closed:
        by_day[_day(p.updated_ms)] += p.net
    series: list[s.LivePnlPoint] = []
    running = Decimal(0)
    for day in sorted(by_day):
        running += by_day[day]
        series.append(s.LivePnlPoint(day=day, net=float(by_day[day]), cumulative=float(running)))
    open_ = [p for p in snap.positions if p.status == "open"]
    severities = Counter(h.finding.severity.value for h in ctx.recon().findings)
    latest = snap.balances[-1] if snap.balances else None
    return s.LiveOverview(
        source=src,
        margin_coin=latest.margin_coin if latest else None,
        balance=_balance_point(latest) if latest else None,
        balance_series=[_balance_point(b) for b in snap.balances],
        totals=_totals(closed),
        totals_30d=_totals(p for p in closed if p.updated_ms >= ctx.now_ms - RECENT_MS),
        pnl_series=series,
        open_positions=len(open_),
        open_unrealized=float(sum((p.unrealized_pnl or Decimal(0) for p in open_), Decimal(0))),
        fills=len(snap.fills),
        orders=snap.orders,
        findings=dict(severities),
        last_run=_run(snap.runs[0]) if snap.runs else None,
    )


# -------------------------------------------------------------- positions


def _position(snap: LiveSnapshot, p: Position, codes: dict[str, list[str]]) -> s.LivePosition:
    return s.LivePosition(
        position_id=p.position_id,
        symbol=p.symbol,
        side=p.side,
        status=p.status,
        qty=float(p.qty),
        entry_price=float(p.entry_price),
        close_price=_f(p.close_price),
        fee=float(p.fee),
        funding=float(p.funding),
        realized=float(p.realized_pnl),
        net=float(p.net),
        unrealized=_f(p.unrealized_pnl),
        leverage=p.leverage,
        opened_ms=p.opened_ms,
        closed_ms=p.updated_ms if p.status == "closed" else None,
        origin=_origin(snap, "position", p.position_id),
        findings=sorted(set(codes.get(p.position_id, []))),
        realized_original=_f(_originals(snap).get(p.position_id)),
    )


def _originals(snap: LiveSnapshot) -> dict[str, Decimal]:
    """Position id → the export's own gross P/L, where the stored one replaced it.

    Only for copies the export wrote: an API record kept its own figure.
    """
    return {
        c.record_key: c.original
        for c in snap.checks
        if c.code == Code.EXPORT_PNL_INCONSISTENT.value
        and c.original is not None
        and _origin(snap, "position", c.record_key) == "csv"
    }


def positions(
    ctx: LiveContext, *, symbol: str | None = None, limit: int = 50, offset: int = 0
) -> s.LivePositionsView:
    src = source(ctx)
    snap = ctx.snap
    if not isinstance(snap, LiveSnapshot):
        return s.LivePositionsView(
            source=src, open=[], closed=[], total_closed=0, offset=offset, limit=limit, symbols=[]
        )
    codes: dict[str, list[str]] = defaultdict(list)
    for h in ctx.recon().findings:
        if h.finding.position_id:
            codes[h.finding.position_id].append(h.finding.code.value)
    wanted = [p for p in snap.positions if symbol is None or p.symbol == symbol]
    open_ = sorted((p for p in wanted if p.status == "open"), key=lambda p: -p.opened_ms)
    closed = sorted(
        (p for p in wanted if p.status == "closed"), key=lambda p: (-p.updated_ms, p.position_id)
    )
    return s.LivePositionsView(
        source=src,
        open=[_position(snap, p, codes) for p in open_],
        closed=[_position(snap, p, codes) for p in closed[offset : offset + limit]],
        total_closed=len(closed),
        offset=offset,
        limit=limit,
        symbols=sorted({p.symbol for p in snap.positions}),
    )


# ----------------------------------------------------------------- trades


def _fill(snap: LiveSnapshot, f: Fill) -> s.LiveFill:
    return s.LiveFill(
        trade_id=f.trade_id,
        order_id=f.order_id,
        position_id=f.position_id,
        symbol=f.symbol,
        side=f.side,
        qty=float(f.qty),
        price=float(f.price),
        fee=float(f.fee),
        realized=float(f.realized_pnl),
        role=f.role,
        ts_ms=f.ts_ms,
        origin=_origin(snap, "fill", f.trade_id),
    )


def trades(
    ctx: LiveContext, *, symbol: str | None = None, limit: int = 100, offset: int = 0
) -> s.LiveTradesView:
    src = source(ctx)
    snap = ctx.snap
    if not isinstance(snap, LiveSnapshot):
        return s.LiveTradesView(
            source=src, fills=[], total=0, offset=offset, limit=limit, symbols=[], fees=0.0
        )
    wanted = sorted(
        (f for f in snap.fills if symbol is None or f.symbol == symbol),
        key=lambda f: (-f.ts_ms, f.trade_id),
    )
    return s.LiveTradesView(
        source=src,
        fills=[_fill(snap, f) for f in wanted[offset : offset + limit]],
        total=len(wanted),
        offset=offset,
        limit=limit,
        symbols=sorted({f.symbol for f in snap.fills}),
        fees=float(sum((f.fee for f in wanted), Decimal(0))),
    )


# ------------------------------------------------------------------ costs


def _cost_row(key: str, ps: list[Position]) -> s.LiveCostRow:
    realized = sum((p.realized_pnl for p in ps), Decimal(0))
    fees = sum((p.fee for p in ps), Decimal(0))
    paid = sum((p.funding for p in ps if p.funding < 0), Decimal(0))
    received = sum((p.funding for p in ps if p.funding > 0), Decimal(0))
    gross = sum((abs(p.realized_pnl) for p in ps), Decimal(0))
    return s.LiveCostRow(
        key=key,
        positions=len(ps),
        realized=float(realized),
        fees=float(fees),
        funding_paid=float(paid),
        funding_received=float(received),
        funding=float(paid + received),
        net=float(realized - fees + paid + received),
        fee_share=float(fees / gross) if gross else None,
    )


def costs(ctx: LiveContext) -> s.LiveCostsView:
    src = source(ctx)
    snap = ctx.snap
    if not isinstance(snap, LiveSnapshot):
        return s.LiveCostsView(
            source=src,
            totals=_totals([]),
            by_month=[],
            by_symbol=[],
            maker_fills=0,
            taker_fills=0,
            unknown_role_fills=0,
        )
    closed = _closed(snap)
    months: dict[str, list[Position]] = defaultdict(list)
    symbols: dict[str, list[Position]] = defaultdict(list)
    for p in closed:
        months[_day(p.updated_ms)[:7]].append(p)
        symbols[p.symbol].append(p)
    roles = Counter(f.role for f in snap.fills)
    gross = sum((abs(p.realized_pnl) for p in closed), Decimal(0))
    fees = sum((p.fee for p in closed), Decimal(0))
    return s.LiveCostsView(
        source=src,
        totals=_totals(closed),
        by_month=[_cost_row(k, months[k]) for k in sorted(months, reverse=True)],
        by_symbol=sorted(
            (_cost_row(k, v) for k, v in symbols.items()), key=lambda r: r.fees, reverse=True
        ),
        fee_share=float(fees / gross) if gross else None,
        maker_fills=roles.get("maker", 0),
        taker_fills=roles.get("taker", 0),
        unknown_role_fills=sum(n for r, n in roles.items() if r not in ("maker", "taker")),
    )


# --------------------------------------------------------- reconciliation


def _default_journal(snap: LiveSnapshot) -> str | None:
    return "control" if "control" in snap.journals else next(iter(sorted(snap.journals)), None)


def _reconcile(snap: LiveSnapshot, journal: str | None) -> _Recon:
    # An export without ids cannot carry fills, so "no fills archived" says nothing there.
    out: list[_Hit] = [
        _Hit("internal", f)
        for f in check_internal(snap.positions, snap.fills)
        if not (f.code is Code.FILLS_MISSING and f.position_id and is_derived(f.position_id))
    ]
    span: tuple[int, int] | None = None
    basis: str | None = None
    compared = entries_n = matched = 0
    if journal is not None:
        entries: tuple[JournalEntry, ...] = snap.journals[journal]
        if entries:
            span = (min(e.opened_ms for e in entries), max(e.opened_ms for e in entries))
            slack = DEFAULT_TOLERANCES.time_ms
            # Only the venue positions the journal could know: those opened in its span.
            venue = [p for p in snap.positions if span[0] - slack <= p.opened_ms <= span[1] + slack]
            result = reconcile_journal(venue, entries)
            out += [_Hit("journal", f) for f in result.findings]
            compared, entries_n, matched = len(venue), len(entries), len(result.pairs)
            bases = {e.pnl_basis.value for e in entries if e.pnl is not None}
            basis = bases.pop() if len(bases) == 1 else ("mixed" if bases else None)
    checks = 0
    for i in range(1, len(snap.balances)):
        before, after = snap.balances[i - 1], snap.balances[i]
        if before.margin_coin != after.margin_coin:
            continue
        then, now = snap.positions_at[i - 1], snap.positions_at[i]
        # A position archived later (a CSV backfill) but closed before the earlier
        # observation did not move the balance in between.
        now = {
            k: p
            for k, p in now.items()
            if k in then or p.status == "open" or p.updated_ms >= before.ts_ms
        }
        checks += 1
        period = (before.ts_ms, after.ts_ms)
        out += [_Hit("balance", f, period) for f in check_balance(before, after, then, now)]
    out += _export_hits(snap)
    totals = check_fill_totals(
        snap.fill_totals, snap.fills, snap.coverage.get(Stream.FILLS.value, []), snap.positions
    )
    out += [_Hit("totals", f, f.period) for f in totals]
    return _Recon(out, journal, span, basis, compared, entries_n, matched, checks)


def _export_hits(snap: LiveSnapshot) -> list[_Hit]:
    """What exports without ids say against themselves and against the API."""
    out: list[_Hit] = []
    by_id = _by_position(snap)
    for c in snap.checks:
        if c.code != Code.EXPORT_PNL_INCONSISTENT.value:
            continue
        key = snap.aliases.get(c.record_key, c.record_key)
        pos = by_id.get(key)
        finding = Finding(
            Code.EXPORT_PNL_INCONSISTENT,
            Severity.WARNING,
            key,
            symbol=pos.symbol if pos else None,
            venue_value=c.stated,
            other_value=c.derived,
            field="net",
        )
        out.append(_Hit("export", finding))
    for api_id, copy in sorted(snap.twins.items()):
        api = by_id.get(api_id)
        if api is not None:
            out += [_Hit("export", f) for f in compare_twin(api, copy)]
    return out


_FIELD: Final = {
    "fill_fee_mismatch": "fee",
    "fill_pnl_mismatch": "realized",
    "fee_mismatch": "fee",
    "funding_mismatch": "funding",
    "pnl_mismatch": "pnl",
    "price_mismatch": "close_price",
    "status_mismatch": "status",
    "unexplained_balance_change": "wallet",
    "export_pnl_inconsistent": "net",
}
#: Fields a finding names itself (``Finding.field``), for the labels.
_OWN_FIELDS: Final = ("qty", "entry_price", "close_price", "fee", "funding", "realized")
_OTHER: Final = {
    "internal": "fills",
    "journal": "journal",
    "balance": "trade_history",
    "totals": "fill_totals",
}
_OTHER_BY_CODE: Final = {
    "export_pnl_inconsistent": "export_components",
    "export_differs_from_api": "export",
}


def _finding(snap: LiveSnapshot, rec: _Recon, hit: _Hit) -> s.LiveFinding:
    f = hit.finding
    pos = _by_position(snap).get(f.position_id) if f.position_id else None
    entry = _by_entry(snap, rec.journal).get(f.entry_id) if f.entry_id and rec.journal else None
    fills: list[str] = []
    if hit.group == "internal" and f.position_id:
        fills = sorted(x.trade_id for x in snap.fills if x.position_id == f.position_id)
    opened = pos.opened_ms if pos else (entry.opened_ms if entry else None)
    if pos is not None:
        closed = pos.updated_ms if pos.status == "closed" else None
    else:
        closed = entry.closed_ms if entry else None
    basis = None
    if f.code.value == "pnl_mismatch" and entry is not None:
        basis = entry.pnl_basis.value
    period = hit.period or f.period
    ident = ":".join(
        str(x)
        for x in (
            hit.group,
            f.code.value,
            f.position_id or "",
            f.entry_id or "",
            f.symbol if hit.group == "totals" else "",
            f.field or "",
            period[1] if period else "",
        )
    )
    if hit.group == "balance":
        venue_source: str | None = "api"
    elif f.code is Code.EXPORT_PNL_INCONSISTENT:
        venue_source = "csv"
    elif pos is not None:
        venue_source = _origin(snap, "position", pos.position_id)
    else:
        venue_source = None
    return s.LiveFinding(
        id=ident,
        code=f.code.value,
        severity=f.severity.value,
        group=hit.group,
        field=f.field or _FIELD.get(f.code.value),
        position_id=f.position_id,
        entry_id=f.entry_id,
        symbol=f.symbol,
        side=pos.side if pos else (entry.side.lower() if entry else None),
        opened_ms=opened,
        closed_ms=closed,
        venue_source=venue_source,
        other_source=_OTHER_BY_CODE.get(f.code.value) or _OTHER[hit.group],
        journal=rec.journal if hit.group == "journal" else None,
        basis=basis,
        fill_ids=fills,
        period_from_ms=period[0] if period else None,
        period_to_ms=period[1] if period else None,
        venue_value=_f(f.venue_value),
        other_value=_f(f.other_value),
        difference=_f(f.difference),
        ts_ms=period[1] if period else None,
    )


def _by_position(snap: LiveSnapshot) -> dict[str, Position]:
    return {p.position_id: p for p in snap.positions}


def _by_entry(snap: LiveSnapshot, journal: str) -> dict[str, JournalEntry]:
    return {e.entry_id: e for e in snap.journals.get(journal, ())}


_SEVERITY_ORDER: Final = {"error": 0, "warning": 1, "info": 2}


def reconciliation(ctx: LiveContext, *, journal: str | None = None) -> s.LiveReconciliationView:
    src = source(ctx)
    snap = ctx.snap
    rec = ctx.recon(journal)
    findings = (
        sorted(
            (_finding(snap, rec, h) for h in rec.findings),
            key=lambda f: (
                _SEVERITY_ORDER.get(f.severity, 3),
                f.group,
                f.code,
                f.symbol or "",
                f.position_id or f.entry_id or "",
            ),
        )
        if isinstance(snap, LiveSnapshot)
        else []
    )
    return s.LiveReconciliationView(
        source=src,
        journals=sorted(snap.journals) if isinstance(snap, LiveSnapshot) else [],
        journal=rec.journal,
        journal_from_ms=rec.journal_span[0] if rec.journal_span else None,
        journal_to_ms=rec.journal_span[1] if rec.journal_span else None,
        pnl_basis=rec.pnl_basis,
        positions_compared=rec.positions_compared,
        entries_compared=rec.entries_compared,
        matched=rec.matched,
        balance_checks=rec.balance_checks,
        counts=dict(Counter(f.code for f in findings)),
        findings=findings[:MAX_FINDINGS_SHOWN],
    )


# ---------------------------------------------------------------- sources


def _import(snap: LiveSnapshot, run: Any) -> s.LiveImport:
    try:
        detail = json.loads(run.detail) if run.detail else {}
    except ValueError:  # a detail this version did not write: shown without it
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    issues = snap.issues.get(run.id, [])
    parts = str(run.source).split(":")
    return s.LiveImport(
        run=_run(run),
        kind=str(detail.get("kind") or (parts[1] if len(parts) > 1 else "")),
        file_digest=str(detail.get("file_sha256") or (parts[2] if len(parts) > 2 else ""))[:12],
        rows=detail.get("rows") if isinstance(detail.get("rows"), int) else None,
        pnl_basis=detail.get("pnl_basis"),
        pnl_basis_source=detail.get("pnl_basis_source"),
        issue_counts=dict(Counter(code for _, code, _, _ in issues)),
        issues=[
            s.LiveImportIssue(line=line, code=code, severity=sev, field=fld)
            for line, code, sev, fld in issues[:MAX_ISSUES_SHOWN]
        ],
    )


def sources(ctx: LiveContext) -> s.LiveSourcesView:
    src = source(ctx)
    snap = ctx.snap
    if not isinstance(snap, LiveSnapshot):
        return s.LiveSourcesView(
            source=src, counts={}, runs=[], imports=[], coverage=[], since_ms=ctx.now_ms
        )
    gaps = _gaps(snap, ctx.now_ms)
    coverage = [
        s.LiveCoverage(
            stream=stream.value,
            intervals=[
                s.LiveInterval(from_ms=a, to_ms=b) for a, b in snap.coverage.get(stream, [])
            ],
            gaps=[g for g in gaps if g.stream == stream.value],
        )
        for stream in Stream
    ]
    return s.LiveSourcesView(
        source=src,
        counts={
            "position": len(snap.positions),
            "fill": len(snap.fills),
            "order": snap.orders,
            "balance": len(snap.balances),
            "journal_entry": sum(len(v) for v in snap.journals.values()),
            "fill_total": len(snap.fill_totals),
        },
        runs=[_run(r) for r in snap.runs],
        imports=[_import(snap, r) for r in snap.runs if r.kind == "import"][:20],
        coverage=coverage,
        since_ms=_since(snap) or ctx.now_ms,
    )


__all__ = [
    "LiveContext",
    "context",
    "costs",
    "overview",
    "positions",
    "reconciliation",
    "source",
    "sources",
    "trades",
]
