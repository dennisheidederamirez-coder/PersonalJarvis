"""Wire models of the trading dashboard (``/api/trading``).

Mirrored field by field in ``frontend/src/types/trading.ts``; the parity test
``tests/unit/trading_dashboard/test_schema_parity.py`` keeps the two equal
(AP-4). Amounts are in the paper accounts' quote currency (USD), fractions
are plain floats (0.012 = 1.2 %), times are epoch milliseconds UTC.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

Param = float | int | bool | str


class Source(BaseModel):
    """Where the numbers came from and whether they can be trusted right now."""

    state: str
    """ok / missing / busy / invalid / error (``reader.SourceState``)."""
    detail: str
    file_name: str
    read_at_ms: int
    journal_mtime_ms: int | None = None
    size_bytes: int | None = None
    read_ms: float | None = None
    truncated: bool = False
    bad_rows: int = 0
    warnings: list[str] = Field(default_factory=list)
    """Codes: spec_mismatch, run_overdue, runs_missed, data_problem,
    kill_switch, rows_truncated, bad_rows, job_refused."""


class PaperTestStatus(BaseModel):
    spec_name: str
    spec_digest: str
    spec_matches: bool
    """The journal was started with the spec this app knows."""
    state: str
    """not_enabled / running / finished / disabled / refused."""
    from_ms: int | None = None
    until_ms: int | None = None
    day: int = 0
    days_total: int = 0
    progress: float = 0.0
    runs_done: int = 0
    runs_expected: int = 0
    runs_missed: int = 0
    last_run_ms: int | None = None
    next_run_ms: int | None = None
    code_digest: str = ""
    refusals: int = 0


class Account(BaseModel):
    candidate: str
    streams: list[str]
    capital: float
    cash: float
    equity: float
    return_pct: float
    realized: float
    unrealized: float
    fees: float
    funding: float
    day_pnl: float
    peak_equity: float
    drawdown_now: float
    max_drawdown: float
    kill_switch: bool
    kill_reason: str
    halted_day: str | None = None
    trades: int
    open_positions: int
    evaluable: bool
    min_trades: int


class Combined(BaseModel):
    capital: float
    equity: float
    return_pct: float
    realized: float
    unrealized: float
    open_risk: float
    same_symbol_overlap: list[str]


class Overview(BaseModel):
    source: Source
    test: PaperTestStatus
    accounts: list[Account]
    combined: Combined


class Decision(BaseModel):
    id: int
    ts_ms: int
    """When it happened: a decision at its bar's close, an execution at the
    bar's open, a job note at the run."""
    kind: str
    group: str
    """signal / execution / no_trade / risk."""
    symbol: str
    stream: str | None = None
    candidate: str | None = None
    reason_code: str
    reason: str
    signal_code: str | None = None
    """For approvals, rejections and fills: the signal that led to them."""
    signal_reason: str | None = None
    side: str | None = None
    qty: float | None = None
    price: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    risk: float | None = None
    net: float | None = None
    r_multiple: float | None = None


class StreamStatus(BaseModel):
    id: str
    candidate: str
    symbol: str
    interval: str
    strategy: str
    params: dict[str, Param]
    status: str
    """position_open / order_pending / waiting / no_signal / data_problem /
    kill_switch / not_started."""
    data_status: str
    last_close_ms: int | None = None
    """Close time of the newest processed bar (the journal stores open times)."""
    next_close_ms: int | None = None
    last_event: Decision | None = None
    position_side: str | None = None
    trades: int = 0
    net: float = 0.0


class StrategiesView(BaseModel):
    source: Source
    streams: list[StreamStatus]


class Position(BaseModel):
    candidate: str
    stream: str | None = None
    symbol: str
    side: str
    qty: float
    entry: float
    mark: float
    stop: float
    take_profit: float | None = None
    unrealized: float
    risk_at_stop: float
    notional: float
    funding: float
    opened_ms: int
    leverage: float


class PendingOrder(BaseModel):
    candidate: str
    symbol: str
    stream: str | None = None
    action: str
    """entry / exit / exit_and_entry."""
    side: str | None = None
    qty: float = 0.0
    reason: str = ""
    reason_code: str = "other"


class LimitUse(BaseModel):
    key: str
    """open_risk / gross_exposure / positions / daily_loss / drawdown."""
    used: float
    limit: float
    ratio: float


class AccountRisk(BaseModel):
    candidate: str
    equity: float
    limits: list[LimitUse]
    kill_switch: bool
    kill_reason: str
    halted_day: str | None = None


class PositionsView(BaseModel):
    source: Source
    positions: list[Position]
    pending: list[PendingOrder]
    risk: list[AccountRisk]
    same_symbol_overlap: list[str]
    open_risk: float
    notional: float


class ReasonCount(BaseModel):
    kind: str
    reason_code: str
    count: int


class DecisionPage(BaseModel):
    source: Source
    items: list[Decision]
    total: int
    next_before_id: int | None = None
    reason_counts: list[ReasonCount]
    streams: list[str]


class PerfRow(BaseModel):
    key: str
    candidate: str
    symbol: str | None = None
    strategy: str | None = None
    trades: int
    wins: int
    losses: int
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_r: float | None = None
    net: float
    fees: float
    funding: float
    best: float | None = None
    worst: float | None = None
    avg_hold_h: float | None = None


class EquityPoint(BaseModel):
    ts_ms: int
    equity: float


class EquitySeries(BaseModel):
    candidate: str
    points: list[EquityPoint]


class Criteria(BaseModel):
    profit_factor_min: float
    avg_r_min: float
    max_drawdown_max: float
    min_trades: int


class TradeRow(BaseModel):
    id: int
    candidate: str | None = None
    stream: str | None = None
    symbol: str
    side: str
    entry_ms: int
    exit_ms: int
    entry: float
    exit: float
    net: float
    fees: float
    r_multiple: float
    exit_code: str
    exit_reason: str


class PerformanceView(BaseModel):
    source: Source
    by_stream: list[PerfRow]
    by_candidate: list[PerfRow]
    by_symbol: list[PerfRow]
    equity: list[EquitySeries]
    criteria: Criteria
    trades: list[TradeRow]


class StreamHealth(BaseModel):
    id: str
    symbol: str
    interval: str
    data_status: str
    data_code: str
    last_close_ms: int | None = None
    expected_close_ms: int
    """Close time of the newest bar that has closed by now."""
    lag_bars: int | None = None
    stale: bool


class JobRun(BaseModel):
    ts_ms: int
    processed: int
    skipped: dict[str, str]
    requests: int
    notes: list[str]


class Incident(BaseModel):
    id: int
    ts_ms: int
    kind: str
    symbol: str
    reason_code: str
    reason: str


class HealthView(BaseModel):
    source: Source
    streams: list[StreamHealth]
    runs: list[JobRun]
    requests_today: int
    request_budget_day: int
    request_budget_run: int
    incidents: list[Incident]
    last_run_ms: int | None = None
    next_run_ms: int | None = None
    run_overdue: bool


class ReportCandidate(BaseModel):
    candidate: str
    equity: float
    return_pct: float
    realized_today: float
    realized_total: float
    unrealized: float
    fees_today: float
    trades_today: int
    trades_total: int
    drawdown_now: float
    kill_switch: bool
    open_positions: int


class DailyReport(BaseModel):
    day: str
    runs: int
    expected_runs: int
    requests: int
    notes: list[str]
    refusals: list[str]
    candidates: list[ReportCandidate]
    combined_equity: float | None = None
    open_risk: float | None = None
    same_symbol_overlap: list[str]
    data: dict[str, str]


class ReportsView(BaseModel):
    source: Source
    reports: list[DailyReport]


# --------------------------------------------------------------------- LIVE
#
# The LIVE history (``/api/trading/live/*``) reads a different file — the
# archive of ``jarvis.live_history`` — and never shares a model with the
# paper views above: a paper number can never land in a LIVE field. Amounts
# are in the margin coin (USDT); the venue's realized P/L EXCLUDES fees and
# funding, ``net`` includes both; ``funding`` is signed (negative = paid).


class LiveSource(BaseModel):
    """Where the LIVE numbers came from and how complete they are."""

    state: str
    """ok / missing / busy / invalid / error (``reader.SourceState``)."""
    detail: str
    file_name: str
    read_at_ms: int
    archive_mtime_ms: int | None = None
    size_bytes: int | None = None
    read_ms: float | None = None
    last_sync_ms: int | None = None
    """End of the last successful API sync (``None`` = never)."""
    last_import_ms: int | None = None
    origin: str = "unknown"
    """real / synthetic / unknown — synthetic means generated demo data, never the account."""
    warnings: list[str] = Field(default_factory=list)
    """Codes: no_sync_yet, last_run_failed, last_run_partial, sync_old,
    coverage_gaps, import_issues, records_truncated, origin_unknown."""


class LiveTotals(BaseModel):
    realized: float
    """The venue's realized P/L: excludes fees and funding."""
    fees: float
    funding: float
    net: float
    closed: int
    wins: int
    losses: int
    win_rate: float | None = None


class LiveBalancePoint(BaseModel):
    ts_ms: int
    wallet: float
    """available + frozen + margin: what fees, funding and closes move."""
    available: float
    unrealized: float


class LivePnlPoint(BaseModel):
    day: str
    """UTC day the positions closed (YYYY-MM-DD)."""
    net: float
    cumulative: float


class LiveRun(BaseModel):
    id: int
    kind: str
    """sync / backfill / import."""
    source: str
    """simulated, api, csv:positions, csv:fills, csv:journal …"""
    started_ms: int
    finished_ms: int | None = None
    status: str
    """running / ok / partial / failed."""
    requests: int
    inserted: int
    updated: int
    unchanged: int
    rejected: int
    error_code: str | None = None


class LiveOverview(BaseModel):
    source: LiveSource
    margin_coin: str | None = None
    balance: LiveBalancePoint | None = None
    balance_series: list[LiveBalancePoint]
    totals: LiveTotals
    totals_30d: LiveTotals
    pnl_series: list[LivePnlPoint]
    open_positions: int
    open_unrealized: float
    fills: int
    orders: int
    findings: dict[str, int]
    """Reconciliation findings per severity (error / warning / info)."""
    last_run: LiveRun | None = None


class LivePosition(BaseModel):
    position_id: str
    symbol: str
    side: str
    status: str
    """open / closed."""
    qty: float
    entry_price: float
    close_price: float | None = None
    fee: float
    funding: float
    realized: float
    net: float
    unrealized: float | None = None
    leverage: int | None = None
    opened_ms: int
    closed_ms: int | None = None
    origin: str
    """api / csv — where the stored copy came from."""
    findings: list[str]
    """Reconciliation codes that concern this position."""
    realized_original: float | None = None
    """When the export contradicted itself: its own gross P/L, replaced by the
    one its stated net implies (``realized``); kept for control."""


class LivePositionsView(BaseModel):
    source: LiveSource
    open: list[LivePosition]
    closed: list[LivePosition]
    total_closed: int
    offset: int
    limit: int
    symbols: list[str]


class LiveFill(BaseModel):
    trade_id: str
    order_id: str
    position_id: str | None = None
    symbol: str
    side: str | None = None
    qty: float
    price: float
    fee: float
    realized: float
    role: str | None = None
    ts_ms: int
    origin: str


class LiveTradesView(BaseModel):
    source: LiveSource
    fills: list[LiveFill]
    total: int
    offset: int
    limit: int
    symbols: list[str]
    fees: float
    """Fees of every fill that matches the filter (not only this page)."""


class LiveCostRow(BaseModel):
    key: str
    """A month (YYYY-MM, UTC) or a symbol."""
    positions: int
    realized: float
    fees: float
    funding_paid: float
    funding_received: float
    funding: float
    net: float
    fee_share: float | None = None
    """Fees as a share of the gross result's absolute value."""


class LiveCostsView(BaseModel):
    source: LiveSource
    totals: LiveTotals
    by_month: list[LiveCostRow]
    by_symbol: list[LiveCostRow]
    fee_share: float | None = None
    """All fees as a share of the gross results' absolute value."""
    maker_fills: int
    taker_fills: int
    unknown_role_fills: int


class LiveFinding(BaseModel):
    """One difference, traceable to the records it is about.

    ``venue_value`` is the exchange's figure (from ``venue_source``),
    ``other_value`` the figure it was compared with (from ``other_source``),
    ``difference`` = venue − other. The UI explains each ``code`` in words.
    """

    id: str
    """Stable within one archive state: group, code and the records involved."""
    code: str
    """A ``reconcile.Code`` value."""
    severity: str
    """error / warning / info."""
    group: str
    """internal / journal / balance / export / totals."""
    field: str | None = None
    """What was compared: fee / realized / funding / pnl / net / qty / entry_price /
    close_price / status / wallet."""
    position_id: str | None = None
    entry_id: str | None = None
    symbol: str | None = None
    side: str | None = None
    opened_ms: int | None = None
    """Open time of the position (or of the journal entry when there is no position)."""
    closed_ms: int | None = None
    venue_source: str | None = None
    """api / csv — where the archived exchange record came from (None for totals)."""
    other_source: str
    """fills / journal / trade_history / export / export_components / fill_totals —
    what the exchange figure was compared with."""
    journal: str | None = None
    basis: str | None = None
    """For P/L: the definition both sides were aligned to (the journal's)."""
    fill_ids: list[str] = Field(default_factory=list)
    """For venue-internal findings: the fills that were summed."""
    period_from_ms: int | None = None
    """For balance findings the two observations compared; for totals the days."""
    period_to_ms: int | None = None
    venue_value: float | None = None
    other_value: float | None = None
    difference: float | None = None
    ts_ms: int | None = None


class LiveReconciliationView(BaseModel):
    source: LiveSource
    journals: list[str]
    journal: str | None = None
    """The journal compared (``None`` = none imported)."""
    journal_from_ms: int | None = None
    journal_to_ms: int | None = None
    pnl_basis: str | None = None
    """The P/L definition the journal's entries use."""
    positions_compared: int
    entries_compared: int
    matched: int
    balance_checks: int
    counts: dict[str, int]
    """Findings per code."""
    findings: list[LiveFinding]


class LiveInterval(BaseModel):
    from_ms: int
    to_ms: int


class LiveGap(BaseModel):
    stream: str
    from_ms: int
    to_ms: int
    route: str
    """api (the venue still serves it) / export (only its CSV export can)."""
    note: str


class LiveCoverage(BaseModel):
    stream: str
    """positions / fills / orders."""
    intervals: list[LiveInterval]
    gaps: list[LiveGap]


class LiveImportIssue(BaseModel):
    line: int | None = None
    code: str
    severity: str
    field: str | None = None


class LiveImport(BaseModel):
    run: LiveRun
    kind: str
    """positions / fills / journal."""
    file_digest: str
    """First 12 hex digits of the file's SHA-256 (never its name or path)."""
    rows: int | None = None
    pnl_basis: str | None = None
    pnl_basis_source: str | None = None
    issue_counts: dict[str, int]
    issues: list[LiveImportIssue]


class LiveSourcesView(BaseModel):
    source: LiveSource
    counts: dict[str, int]
    """Records per entity, plus journal entries."""
    runs: list[LiveRun]
    imports: list[LiveImport]
    coverage: list[LiveCoverage]
    since_ms: int
    """The start of the coverage check."""


__all__ = [
    "Account",
    "AccountRisk",
    "Combined",
    "Criteria",
    "DailyReport",
    "Decision",
    "DecisionPage",
    "EquityPoint",
    "EquitySeries",
    "HealthView",
    "Incident",
    "JobRun",
    "LimitUse",
    "LiveBalancePoint",
    "LiveCostRow",
    "LiveCostsView",
    "LiveCoverage",
    "LiveFill",
    "LiveFinding",
    "LiveGap",
    "LiveImport",
    "LiveImportIssue",
    "LiveInterval",
    "LiveOverview",
    "LivePnlPoint",
    "LivePosition",
    "LivePositionsView",
    "LiveReconciliationView",
    "LiveRun",
    "LiveSource",
    "LiveSourcesView",
    "LiveTotals",
    "LiveTradesView",
    "Overview",
    "PendingOrder",
    "PerfRow",
    "PerformanceView",
    "Position",
    "PositionsView",
    "ReasonCount",
    "ReportCandidate",
    "ReportsView",
    "Source",
    "StrategiesView",
    "StreamHealth",
    "StreamStatus",
    "PaperTestStatus",
    "TradeRow",
]
