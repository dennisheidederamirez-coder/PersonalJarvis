/**
 * Pieces shared by the LIVE history's tabs: its own source notice and warning
 * list (LIVE wording, never the paper journal's), a severity badge, an origin
 * tag and a small line chart. Theme tokens only — light and dark mode alike.
 */
import { useMemo, type ReactNode } from "react";
import { AlertTriangle, Info } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cn } from "@/lib/utils";
import type { TradingLiveSource } from "@/types/trading";
import type { TradingT } from "../tradingI18n";

export function LiveSourceNotice({ source, t }: { source: TradingLiveSource; t: TradingT }) {
  if (source.state === "ok") return null;
  return (
    <div
      role="status"
      data-testid="live-source-notice"
      className={cn(
        "flex items-start gap-3 rounded-lg border px-4 py-3 text-sm",
        source.state === "busy"
          ? "border-border bg-card text-muted-foreground"
          : "border-warning/40 bg-warning/10 text-foreground",
      )}
    >
      {source.state === "busy" ? (
        <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      ) : (
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
      )}
      <span>{t(`live.source.${source.state}`, { file: source.file_name, detail: source.detail })}</span>
    </div>
  );
}

export function LiveWarningList({ source, t }: { source: TradingLiveSource; t: TradingT }) {
  if (source.state !== "ok" || source.warnings.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2" data-testid="live-warnings">
      {source.warnings.map((w) => (
        <li
          key={w}
          className={cn(
            "flex items-start gap-3 rounded-lg border px-4 py-2.5 text-sm",
            w === "last_run_failed"
              ? "border-destructive/40 bg-destructive/10"
              : "border-warning/40 bg-warning/10",
          )}
        >
          <AlertTriangle
            className={cn(
              "mt-0.5 h-4 w-4 shrink-0",
              w === "last_run_failed" ? "text-destructive" : "text-warning",
            )}
            aria-hidden
          />
          <span>{t(`live.warning.${w}`)}</span>
        </li>
      ))}
    </ul>
  );
}

export function SeverityBadge({ severity, t }: { severity: string; t: TradingT }) {
  return (
    <span
      className={cn(
        "inline-flex rounded px-1.5 py-0.5 text-xs font-medium",
        severity === "error" && "bg-destructive/15 text-destructive",
        severity === "warning" && "bg-warning/15 text-warning",
        severity !== "error" && severity !== "warning" && "bg-secondary text-muted-foreground",
      )}
    >
      {t(`live.recon.severity.${severity}`)}
    </span>
  );
}

export function OriginTag({ origin, t }: { origin: string; t: TradingT }) {
  return (
    <span className="inline-flex rounded border border-border px-1.5 py-0.5 text-xs text-muted-foreground">
      {t(`live.origin.${origin}`)}
    </span>
  );
}

const AXIS_TINT = "hsl(0 0% 50%)";

/** One line over time; a dashed zero line when the values cross it. */
export function LiveLineChart({
  points,
  format,
  label,
  empty,
  tickFormat,
  color = "hsl(var(--primary))",
  step = false,
  testId,
}: {
  points: { ts: number; value: number }[];
  format: (v: number) => string;
  label: string;
  empty: ReactNode;
  tickFormat: (ts: number) => string;
  color?: string;
  step?: boolean;
  testId?: string;
}) {
  const crosses = useMemo(
    () => points.some((p) => p.value < 0) && points.some((p) => p.value > 0),
    [points],
  );
  if (points.length < 2) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
        {empty}
      </div>
    );
  }
  return (
    <div data-testid={testId} aria-label={label} role="img">
      <ResponsiveContainer
        width="100%"
        height={220}
        minWidth={0}
        initialDimension={{ width: 640, height: 220 }}
      >
        <LineChart data={points} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
          <CartesianGrid vertical={false} stroke="hsl(0 0% 50% / 0.14)" strokeDasharray="2 4" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={tickFormat}
            tick={{ fill: AXIS_TINT, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={48}
            dy={4}
          />
          <YAxis
            width={96}
            domain={["auto", "auto"]}
            tick={{ fill: AXIS_TINT, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => format(v)}
          />
          {crosses ? (
            <ReferenceLine y={0} stroke="hsl(0 0% 50% / 0.4)" strokeDasharray="4 4" />
          ) : null}
          <Tooltip
            labelFormatter={(v) => tickFormat(Number(v))}
            formatter={(v) => [format(Number(v)), label]}
            contentStyle={{
              background: "hsl(var(--popover))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 8,
              fontSize: 12,
              color: "hsl(var(--popover-foreground))",
            }}
          />
          <Line
            type={step ? "stepAfter" : "linear"}
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            dot={points.length <= 12}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
