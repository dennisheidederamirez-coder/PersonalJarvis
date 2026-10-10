/** Daily reports: what the paper job wrote for each finished UTC day. */
import { FactRows, Panel } from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import type { TradingReportsView } from "@/types/trading";
import { Empty } from "./parts";
import { dayLabel, money, pct, toneClass } from "./tradingFormat";
import type { TradingT } from "./tradingI18n";

export function ReportsTab({
  data,
  locale,
  t,
}: {
  data: TradingReportsView;
  locale: string;
  t: TradingT;
}) {
  if (data.reports.length === 0) return <Empty>{t("reports.none")}</Empty>;
  return (
    <div className="flex flex-col gap-4" data-testid="trading-reports">
      {data.reports.map((r) => (
        <Panel key={r.day} className="flex flex-col gap-3 p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-medium text-foreground-strong">{dayLabel(locale, r.day)}</span>
            <span className="text-sm tabular-nums text-muted-foreground">
              {t("reports.runs", { runs: r.runs, expected: r.expected_runs, requests: r.requests })}
            </span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {r.candidates.map((c) => (
              <div key={c.candidate} className="flex flex-col gap-2">
                <span className="text-sm font-medium text-foreground">
                  {t("account.name", { id: c.candidate })}
                </span>
                <FactRows
                  rows={[
                    {
                      label: t("reports.equity"),
                      value: (
                        <span className="tabular-nums">
                          {money(locale, c.equity)}{" "}
                          <span className={toneClass(c.return_pct)}>
                            ({pct(locale, c.return_pct, { signed: true })})
                          </span>
                        </span>
                      ),
                    },
                    {
                      label: t("reports.today"),
                      value: (
                        <span className={cn("tabular-nums", toneClass(c.realized_today))}>
                          {money(locale, c.realized_today, true)}
                        </span>
                      ),
                    },
                    {
                      label: t("reports.trades"),
                      value: `${c.trades_today} / ${c.trades_total}`,
                    },
                    { label: t("reports.fees"), value: money(locale, c.fees_today) },
                    { label: t("reports.open"), value: String(c.open_positions) },
                    {
                      label: t("account.drawdown"),
                      value: c.kill_switch ? (
                        <span className="text-destructive">
                          {pct(locale, c.drawdown_now)} · {t("strategy.state.kill_switch")}
                        </span>
                      ) : (
                        pct(locale, c.drawdown_now)
                      ),
                    },
                  ]}
                />
              </div>
            ))}
          </div>
          {r.combined_equity !== null ? (
            <span className="text-sm text-muted-foreground">
              {t("reports.combined", {
                equity: money(locale, r.combined_equity),
                risk: money(locale, r.open_risk ?? 0),
              })}
            </span>
          ) : null}
          {r.notes.length > 0 || r.refusals.length > 0 ? (
            <span className="text-sm text-warning">
              {t("reports.notes")}: {[...r.notes, ...r.refusals].join(" · ")}
            </span>
          ) : null}
          {Object.values(r.data).some((v) => v !== "ok") ? (
            <span className="text-sm text-warning">
              {t("reports.data")}:{" "}
              {Object.entries(r.data)
                .filter(([, v]) => v !== "ok")
                .map(([k, v]) => `${k} ${v}`)
                .join(", ")}
            </span>
          ) : null}
        </Panel>
      ))}
    </div>
  );
}
