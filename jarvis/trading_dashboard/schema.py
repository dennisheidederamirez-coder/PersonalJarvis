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
    last_bar_ms: int | None = None
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
    last_bar_ms: int | None = None
    expected_bar_ms: int
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
