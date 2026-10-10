/**
 * React Query hooks for the paper-trading dashboard.
 *
 * Endpoints: see `jarvis/ui/web/trading_routes.py`. Every one is a plain GET
 * over the paper job's journal — nothing in this section can change state.
 * The journal changes about six times a day, so a one-minute poll while the
 * section is open is plenty; a closed section polls nothing.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type {
  TradingDecisionPage,
  TradingHealthView,
  TradingOverview,
  TradingPerformanceView,
  TradingPositionsView,
  TradingReportsView,
  TradingStrategiesView,
} from "@/types/trading";

const BASE = "/api/trading";
const POLL_MS = 60_000;

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

function useTradingGet<T>(name: string, enabled = true) {
  return useQuery({
    queryKey: ["trading", name],
    queryFn: ({ signal }) => getJson<T>(`${BASE}/${name}`, signal),
    refetchInterval: POLL_MS,
    staleTime: 15_000,
    enabled,
  });
}

export const useTradingOverview = () => useTradingGet<TradingOverview>("overview");
export const useTradingStrategies = (enabled = true) =>
  useTradingGet<TradingStrategiesView>("strategies", enabled);
export const useTradingPositions = (enabled = true) =>
  useTradingGet<TradingPositionsView>("positions", enabled);
export const useTradingPerformance = (enabled = true) =>
  useTradingGet<TradingPerformanceView>("performance", enabled);
export const useTradingHealth = (enabled = true) =>
  useTradingGet<TradingHealthView>("health", enabled);
export const useTradingReports = (enabled = true) =>
  useTradingGet<TradingReportsView>("reports", enabled);

export interface DecisionQuery {
  group: "all" | "signal" | "execution" | "no_trade" | "risk";
  stream: string | null;
  reasonCode: string | null;
  limit: number;
}

export function decisionParams(q: DecisionQuery): URLSearchParams {
  const params = new URLSearchParams({ group: q.group, limit: String(q.limit) });
  if (q.stream) params.set("stream", q.stream);
  if (q.reasonCode) params.set("reason_code", q.reasonCode);
  return params;
}

export function useTradingDecisions(q: DecisionQuery, enabled = true) {
  const params = decisionParams(q);
  return useQuery({
    queryKey: ["trading", "decisions", params.toString()],
    queryFn: ({ signal }) =>
      getJson<TradingDecisionPage>(`${BASE}/decisions?${params.toString()}`, signal),
    refetchInterval: POLL_MS,
    staleTime: 15_000,
    placeholderData: keepPreviousData,
    enabled,
  });
}
