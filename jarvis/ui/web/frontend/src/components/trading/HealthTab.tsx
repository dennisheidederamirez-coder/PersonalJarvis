/** Data & operations: data freshness per stream, job runs, request budget, incidents. */
import {
  Cell,
  type Column,
  FactRows,
  Panel,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import type { TradingHealthView, TradingSource } from "@/types/trading";
import { Dot, Empty, Meter, ReasonText, Section } from "./parts";
import { bytes, dateTime, num, relative } from "./tradingFormat";
import type { TradingLanguage, TradingT } from "./tradingI18n";

export function HealthTab({
  data,
  source,
  locale,
  lang,
  t,
  now,
}: {
  data: TradingHealthView;
  source: TradingSource;
  locale: string;
  lang: TradingLanguage;
  t: TradingT;
  now: number;
}) {
  const streamColumns: Column[] = [
    { id: "stream", label: t("health.stream"), width: "minmax(110px, 1fr)" },
    { id: "data", label: t("health.data"), width: "minmax(150px, 1.2fr)" },
    { id: "bar", label: t("health.last_bar"), width: "minmax(120px, 1fr)" },
    { id: "lag", label: t("health.lag"), width: "minmax(110px, 0.9fr)" },
  ];
  const runColumns: Column[] = [
    { id: "time", label: t("health.run_time"), width: "minmax(110px, 0.9fr)" },
    { id: "processed", label: t("health.processed"), align: "right", width: "90px" },
    { id: "requests", label: t("health.requests"), align: "right", width: "90px" },
    { id: "notes", label: t("health.notes"), width: "minmax(200px, 2.5fr)" },
  ];
  const incidentColumns: Column[] = [
    { id: "time", label: t("decisions.time"), width: "minmax(110px, 0.8fr)" },
    { id: "kind", label: t("decisions.kind_label"), width: "minmax(150px, 1fr)" },
    { id: "symbol", label: t("strategy.market"), width: "minmax(80px, 0.6fr)" },
    { id: "reason", label: t("decisions.reason"), width: "minmax(200px, 2.4fr)" },
  ];
  const budgetRatio = data.request_budget_day > 0 ? data.requests_today / data.request_budget_day : 0;

  return (
    <div className="flex flex-col gap-6" data-testid="trading-health">
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel className="flex flex-col gap-3 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-foreground-strong">{t("health.budget_title")}</span>
            {data.run_overdue ? (
              <Dot tone="warn" label={t("health.overdue")} />
            ) : (
              <Dot tone="ok" label={t("health.on_time")} />
            )}
          </div>
          <Meter ratio={budgetRatio} label={t("health.budget_title")} />
          <span className="text-sm tabular-nums text-muted-foreground">
            {t("health.budget", {
              used: data.requests_today,
              limit: data.request_budget_day,
              run: data.request_budget_run,
            })}
          </span>
          <FactRows
            rows={[
              {
                label: t("test.last_run"),
                value: data.last_run_ms
                  ? `${dateTime(locale, data.last_run_ms)} (${relative(locale, data.last_run_ms, now)})`
                  : t("test.none"),
              },
              {
                label: t("test.next_run"),
                value: data.next_run_ms
                  ? `${dateTime(locale, data.next_run_ms)} (${relative(locale, data.next_run_ms, now)})`
                  : t("test.none"),
              },
            ]}
          />
        </Panel>
        <Panel className="flex flex-col gap-3 p-4">
          <span className="font-medium text-foreground-strong">{t("health.journal_title")}</span>
          <FactRows
            rows={[
              { label: t("source.file"), value: <span className="font-mono text-sm">{source.file_name}</span> },
              { label: t("source.size"), value: bytes(locale, source.size_bytes) },
              {
                label: t("source.read_time"),
                value: source.read_ms === null ? "—" : `${num(locale, source.read_ms, 1)} ms`,
              },
              { label: t("source.changed"), value: dateTime(locale, source.journal_mtime_ms) },
            ]}
          />
          <span className="text-xs text-foreground-faint">{t("time.utc_note")}</span>
        </Panel>
      </div>

      <Section title={t("health.streams_title")}>
        <Table label={t("health.streams_title")}>
          <TableHead columns={streamColumns} />
          {data.streams.map((s) => (
            <TableRow key={s.id} columns={streamColumns}>
              <Cell>
                <span className="font-mono text-xs">{s.id}</span>
                <span className="ml-2 text-xs text-muted-foreground">{s.interval}</span>
              </Cell>
              <Cell>
                {s.data_status === "ok" ? (
                  <Dot tone="ok" label={t("health.ok")} />
                ) : (
                  <span title={s.data_status}>
                    <Dot tone="warn" label={`${t("reason.data_problem")}: ${s.data_status}`} />
                  </span>
                )}
              </Cell>
              <Cell muted className="tabular-nums">
                {dateTime(locale, s.last_close_ms)}
              </Cell>
              <Cell>
                {s.stale ? (
                  <Dot tone="warn" label={`${t("health.stale")} · ${t("health.lag_value", { n: s.lag_bars ?? "?" })}`} />
                ) : (
                  <Dot tone="ok" label={t("health.current")} />
                )}
              </Cell>
            </TableRow>
          ))}
        </Table>
      </Section>

      <Section title={t("health.runs_title")}>
        {data.runs.length === 0 ? (
          <Empty>{t("health.no_runs")}</Empty>
        ) : (
          <Table label={t("health.runs_title")}>
            <TableHead columns={runColumns} />
            {data.runs.map((r) => {
              const skipped = Object.entries(r.skipped);
              return (
                <TableRow key={r.ts_ms} columns={runColumns}>
                  <Cell muted className="tabular-nums">
                    {dateTime(locale, r.ts_ms)}
                  </Cell>
                  <Cell align="right" className="tabular-nums">
                    {r.processed}
                  </Cell>
                  <Cell align="right" className="tabular-nums">
                    {r.requests}
                  </Cell>
                  <Cell muted>
                    {[
                      ...r.notes,
                      ...(skipped.length > 0
                        ? [`${t("health.skipped")}: ${skipped.map(([k, v]) => `${k} (${v})`).join(", ")}`]
                        : []),
                    ].join(" · ") || "—"}
                  </Cell>
                </TableRow>
              );
            })}
          </Table>
        )}
      </Section>

      <Section title={t("health.incidents_title")}>
        {data.incidents.length === 0 ? (
          <Empty>{t("health.no_incidents")}</Empty>
        ) : (
          <Table label={t("health.incidents_title")}>
            <TableHead columns={incidentColumns} />
            {data.incidents.map((i) => (
              <TableRow key={i.id} columns={incidentColumns}>
                <Cell muted className="tabular-nums">
                  {dateTime(locale, i.ts_ms)}
                </Cell>
                <Cell>{t(`health.incident_kind.${i.kind}`)}</Cell>
                <Cell>{i.symbol === "*" ? "—" : i.symbol}</Cell>
                <Cell>
                  <ReasonText code={i.reason_code} text={i.reason} lang={lang} t={t} />
                </Cell>
              </TableRow>
            ))}
          </Table>
        )}
      </Section>
    </div>
  );
}
