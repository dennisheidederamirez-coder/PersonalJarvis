/**
 * Wire types of the paper-trading dashboard (`/api/trading`).
 *
 * Mirror of `jarvis/trading_dashboard/schema.py`, interface `TradingX` for
 * model `X`, field for field. `tests/unit/trading_dashboard/test_schema_parity.py`
 * keeps the two equal (AP-4). Amounts are in the paper accounts' quote currency
 * (USD), fractions are plain numbers (0.012 = 1.2 %), times are epoch ms UTC.
 */

export type TradingParam = number | boolean | string;

export type TradingSourceState = "ok" | "missing" | "busy" | "invalid" | "error";

export interface TradingSource {
  state: TradingSourceState;
  detail: string;
  file_name: string;
  read_at_ms: number;
  journal_mtime_ms: number | null;
  size_bytes: number | null;
  read_ms: number | null;
  truncated: boolean;
  bad_rows: number;
  /** spec_mismatch, run_overdue, runs_missed, data_problem, kill_switch,
   *  rows_truncated, bad_rows, job_refused */
  warnings: string[];
}

export type TradingTestState = "not_enabled" | "running" | "finished" | "disabled" | "refused";

export interface TradingPaperTestStatus {
  spec_name: string;
  spec_digest: string;
  spec_matches: boolean;
  state: TradingTestState;
  from_ms: number | null;
  until_ms: number | null;
  day: number;
  days_total: number;
  progress: number;
  runs_done: number;
  runs_expected: number;
  runs_missed: number;
  last_run_ms: number | null;
  next_run_ms: number | null;
  code_digest: string;
  refusals: number;
}

export interface TradingAccount {
  candidate: string;
  streams: string[];
  capital: number;
  cash: number;
  equity: number;
  return_pct: number;
  realized: number;
  unrealized: number;
  fees: number;
  funding: number;
  day_pnl: number;
  peak_equity: number;
  drawdown_now: number;
  max_drawdown: number;
  kill_switch: boolean;
  kill_reason: string;
  halted_day: string | null;
  trades: number;
  open_positions: number;
  evaluable: boolean;
  min_trades: number;
}

export interface TradingCombined {
  capital: number;
  equity: number;
  return_pct: number;
  realized: number;
  unrealized: number;
  open_risk: number;
  same_symbol_overlap: string[];
}

export interface TradingOverview {
  source: TradingSource;
  test: TradingPaperTestStatus;
  accounts: TradingAccount[];
  combined: TradingCombined;
}

export type TradingDecisionGroup = "signal" | "execution" | "no_trade" | "risk";

export interface TradingDecision {
  id: number;
  ts_ms: number;
  kind: string;
  group: TradingDecisionGroup;
  symbol: string;
  stream: string | null;
  candidate: string | null;
  reason_code: string;
  reason: string;
  signal_code: string | null;
  signal_reason: string | null;
  side: string | null;
  qty: number | null;
  price: number | null;
  stop: number | null;
  take_profit: number | null;
  risk: number | null;
  net: number | null;
  r_multiple: number | null;
}

export type TradingStreamState =
  | "position_open"
  | "order_pending"
  | "waiting"
  | "no_signal"
  | "data_problem"
  | "kill_switch"
  | "not_started";

export interface TradingStreamStatus {
  id: string;
  candidate: string;
  symbol: string;
  interval: string;
  strategy: string;
  params: Record<string, TradingParam>;
  status: TradingStreamState;
  data_status: string;
  /** Close time of the newest processed bar. */
  last_close_ms: number | null;
  next_close_ms: number | null;
  last_event: TradingDecision | null;
  position_side: string | null;
  trades: number;
  net: number;
}

export interface TradingStrategiesView {
  source: TradingSource;
  streams: TradingStreamStatus[];
}

export interface TradingPosition {
  candidate: string;
  stream: string | null;
  symbol: string;
  side: string;
  qty: number;
  entry: number;
  mark: number;
  stop: number;
  take_profit: number | null;
  unrealized: number;
  risk_at_stop: number;
  notional: number;
  funding: number;
  opened_ms: number;
  leverage: number;
}

export interface TradingPendingOrder {
  candidate: string;
  symbol: string;
  stream: string | null;
  action: "entry" | "exit" | "exit_and_entry";
  side: string | null;
  qty: number;
  reason: string;
  reason_code: string;
}

export type TradingLimitKey =
  | "open_risk"
  | "gross_exposure"
  | "positions"
  | "daily_loss"
  | "drawdown";

export interface TradingLimitUse {
  key: TradingLimitKey;
  used: number;
  limit: number;
  ratio: number;
}

export interface TradingAccountRisk {
  candidate: string;
  equity: number;
  limits: TradingLimitUse[];
  kill_switch: boolean;
  kill_reason: string;
  halted_day: string | null;
}

export interface TradingPositionsView {
  source: TradingSource;
  positions: TradingPosition[];
  pending: TradingPendingOrder[];
  risk: TradingAccountRisk[];
  same_symbol_overlap: string[];
  open_risk: number;
  notional: number;
}

export interface TradingReasonCount {
  kind: string;
  reason_code: string;
  count: number;
}

export interface TradingDecisionPage {
  source: TradingSource;
  items: TradingDecision[];
  total: number;
  next_before_id: number | null;
  reason_counts: TradingReasonCount[];
  streams: string[];
}

export interface TradingPerfRow {
  key: string;
  candidate: string;
  symbol: string | null;
  strategy: string | null;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_r: number | null;
  net: number;
  fees: number;
  funding: number;
  best: number | null;
  worst: number | null;
  avg_hold_h: number | null;
}

export interface TradingEquityPoint {
  ts_ms: number;
  equity: number;
}

export interface TradingEquitySeries {
  candidate: string;
  points: TradingEquityPoint[];
}

export interface TradingCriteria {
  profit_factor_min: number;
  avg_r_min: number;
  max_drawdown_max: number;
  min_trades: number;
}

export interface TradingTradeRow {
  id: number;
  candidate: string | null;
  stream: string | null;
  symbol: string;
  side: string;
  entry_ms: number;
  exit_ms: number;
  entry: number;
  exit: number;
  net: number;
  fees: number;
  r_multiple: number;
  exit_code: string;
  exit_reason: string;
}

export interface TradingPerformanceView {
  source: TradingSource;
  by_stream: TradingPerfRow[];
  by_candidate: TradingPerfRow[];
  by_symbol: TradingPerfRow[];
  equity: TradingEquitySeries[];
  criteria: TradingCriteria;
  trades: TradingTradeRow[];
}

export interface TradingStreamHealth {
  id: string;
  symbol: string;
  interval: string;
  data_status: string;
  data_code: string;
  last_close_ms: number | null;
  expected_close_ms: number;
  lag_bars: number | null;
  stale: boolean;
}

export interface TradingJobRun {
  ts_ms: number;
  processed: number;
  skipped: Record<string, string>;
  requests: number;
  notes: string[];
}

export interface TradingIncident {
  id: number;
  ts_ms: number;
  kind: string;
  symbol: string;
  reason_code: string;
  reason: string;
}

export interface TradingHealthView {
  source: TradingSource;
  streams: TradingStreamHealth[];
  runs: TradingJobRun[];
  requests_today: number;
  request_budget_day: number;
  request_budget_run: number;
  incidents: TradingIncident[];
  last_run_ms: number | null;
  next_run_ms: number | null;
  run_overdue: boolean;
}

export interface TradingReportCandidate {
  candidate: string;
  equity: number;
  return_pct: number;
  realized_today: number;
  realized_total: number;
  unrealized: number;
  fees_today: number;
  trades_today: number;
  trades_total: number;
  drawdown_now: number;
  kill_switch: boolean;
  open_positions: number;
}

export interface TradingDailyReport {
  day: string;
  runs: number;
  expected_runs: number;
  requests: number;
  notes: string[];
  refusals: string[];
  candidates: TradingReportCandidate[];
  combined_equity: number | null;
  open_risk: number | null;
  same_symbol_overlap: string[];
  data: Record<string, string>;
}

export interface TradingReportsView {
  source: TradingSource;
  reports: TradingDailyReport[];
}
