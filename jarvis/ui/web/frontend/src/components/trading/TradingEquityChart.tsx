/**
 * Account value over time, one line per virtual account.
 *
 * Same recharts setup as the Spend section's trend chart: neutral axis tint,
 * a faint dashed grid, no animation, theme-token colours so it reads in light
 * and dark mode. Points sit at each processed bar's close.
 */
import { useMemo } from "react";
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
import type { TradingEquitySeries } from "@/types/trading";
import { dateTime, money } from "./tradingFormat";
import type { TradingT } from "./tradingI18n";

const AXIS_TINT = "hsl(0 0% 50%)";
/** Account colours from theme tokens: primary for A, info for B. */
export const ACCOUNT_COLORS: Record<string, string> = {
  A: "hsl(var(--primary))",
  B: "hsl(var(--info))",
};

interface Row {
  ts: number;
  [account: string]: number;
}

export function TradingEquityChart({
  series,
  capital,
  locale,
  t,
}: {
  series: TradingEquitySeries[];
  capital: number;
  locale: string;
  t: TradingT;
}) {
  // One row per timestamp; an account without a point there carries its last
  // value forward so the lines stay continuous (A trades daily, B every 4 h).
  const data = useMemo<Row[]>(() => {
    const stamps = [...new Set(series.flatMap((s) => s.points.map((p) => p.ts_ms)))].sort(
      (a, b) => a - b,
    );
    const last: Record<string, number> = {};
    const lookup = new Map(
      series.map((s) => [s.candidate, new Map(s.points.map((p) => [p.ts_ms, p.equity]))]),
    );
    return stamps.map((ts) => {
      const row: Row = { ts };
      for (const s of series) {
        const v = lookup.get(s.candidate)?.get(ts);
        if (v !== undefined) last[s.candidate] = v;
        if (last[s.candidate] !== undefined) row[s.candidate] = last[s.candidate];
      }
      return row;
    });
  }, [series]);

  if (data.length < 2) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
        {t("performance.no_trades")}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2" data-testid="trading-equity-chart">
      <ResponsiveContainer
        width="100%"
        height={220}
        minWidth={0}
        initialDimension={{ width: 640, height: 220 }}
      >
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
          <CartesianGrid vertical={false} stroke="hsl(0 0% 50% / 0.14)" strokeDasharray="2 4" />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v: number) => dateTime(locale, v)}
            tick={{ fill: AXIS_TINT, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={48}
            dy={4}
          />
          <YAxis
            width={84}
            domain={["auto", "auto"]}
            tick={{ fill: AXIS_TINT, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => money(locale, v)}
          />
          <ReferenceLine y={capital} stroke="hsl(0 0% 50% / 0.4)" strokeDasharray="4 4" />
          <Tooltip
            labelFormatter={(v) => dateTime(locale, Number(v))}
            formatter={(v, name) => [money(locale, Number(v)), t("account.name", { id: String(name) })]}
            contentStyle={{
              background: "hsl(var(--popover))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 8,
              fontSize: 12,
              color: "hsl(var(--popover-foreground))",
            }}
          />
          {series.map((s) => (
            <Line
              key={s.candidate}
              type="stepAfter"
              dataKey={s.candidate}
              stroke={ACCOUNT_COLORS[s.candidate] ?? AXIS_TINT}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1">
        {series.map((s) => (
          <span key={s.candidate} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className="h-2 w-2 rounded-[2px]"
              style={{ background: ACCOUNT_COLORS[s.candidate] ?? AXIS_TINT }}
            />
            {t("account.name", { id: s.candidate })}
          </span>
        ))}
      </div>
    </div>
  );
}
