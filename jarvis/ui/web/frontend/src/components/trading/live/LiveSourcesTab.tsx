/** LIVE data & imports: what the archive holds, every run, every import's issues, coverage and gaps. */
import {
  Cell,
  type Column,
  FactRows,
  Panel,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import type { TradingLiveRun, TradingLiveSourcesView } from "@/types/trading";
import { Dot, Empty, Section } from "../parts";
import { dateTime } from "../tradingFormat";
import { hasKey, type TradingLanguage, type TradingT } from "../tradingI18n";
import { ScrollTable } from "./liveParts";

const STATUS_TONE = { ok: "ok", partial: "warn", failed: "error", running: "busy" } as const;

export function LiveSourcesTab({
  data,
  locale,
  lang,
  t,
}: {
  data: TradingLiveSourcesView;
  locale: string;
  lang: TradingLanguage;
  t: TradingT;
}) {
  const runColumns: Column[] = [
    { id: "time", label: t("live.sources.col.time"), width: "minmax(100px, 0.8fr)" },
    { id: "kind", label: t("live.sources.col.kind"), width: "minmax(90px, 0.7fr)" },
    { id: "source", label: t("live.sources.col.source"), width: "minmax(110px, 0.8fr)" },
    { id: "status", label: t("live.sources.col.status"), width: "minmax(110px, 0.8fr)" },
    { id: "changes", label: t("live.sources.col.changes"), width: "minmax(220px, 1.6fr)" },
    { id: "rejected", label: t("live.sources.col.rejected"), align: "right", width: "90px" },
    { id: "requests", label: t("live.sources.col.requests"), align: "right", width: "90px" },
    { id: "error", label: t("live.sources.col.error"), width: "minmax(120px, 1fr)" },
  ];
  const issueLabel = (code: string) =>
    hasKey(lang, `live.issue.${code}`) ? t(`live.issue.${code}`) : code;
  const errorLabel = (code: string) =>
    hasKey(lang, `live.sources.error.${code}`) ? t(`live.sources.error.${code}`) : issueLabel(code);
  const status = (r: TradingLiveRun) => (
    <Dot
      tone={STATUS_TONE[r.status as keyof typeof STATUS_TONE] ?? "off"}
      label={t(`live.sources.status.${r.status}`)}
    />
  );

  return (
    <div className="flex flex-col gap-6" data-testid="live-sources">
      <Section title={t("live.sources.counts")}>
        <Panel className="p-5">
          <FactRows
            rows={Object.entries(data.counts).map(([k, n]) => ({
              label: t(`live.sources.entity.${k}`),
              value: <span className="tabular-nums">{n}</span>,
            }))}
          />
        </Panel>
      </Section>

      <Section title={t("live.sources.coverage")}>
        <p className="text-sm text-muted-foreground">
          {t("live.sources.since", { time: dateTime(locale, data.since_ms) })} ·{" "}
          {t("live.sources.how_to")}
        </p>
        <div className="grid gap-4 lg:grid-cols-3">
          {data.coverage.map((c) => (
            <Panel key={c.stream} className="flex flex-col gap-2 p-4" >
              <span className="font-medium text-foreground-strong">
                {t(`live.sources.stream.${c.stream}`)}
              </span>
              {c.intervals.map((iv) => (
                <span key={iv.from_ms} className="text-sm tabular-nums text-foreground">
                  {t("live.sources.covered")}: {dateTime(locale, iv.from_ms)} – {dateTime(locale, iv.to_ms)}
                </span>
              ))}
              {c.gaps.length === 0 ? (
                <Dot tone="ok" label={t("live.sources.gaps_none")} />
              ) : (
                c.gaps.map((g) => (
                  <div key={g.from_ms} className="flex flex-col">
                    <Dot
                      tone={g.route === "api" ? "warn" : "off"}
                      label={`${dateTime(locale, g.from_ms)} – ${dateTime(locale, g.to_ms)}`}
                    />
                    <span className="pl-4 text-xs text-muted-foreground">
                      {t(`live.sources.gap.${g.route}`)}
                      {g.note ? ` · ${t(`live.sources.note.${g.note}`)}` : ""}
                    </span>
                  </div>
                ))
              )}
            </Panel>
          ))}
        </div>
      </Section>

      <Section title={t("live.sources.imports")}>
        {data.imports.length === 0 ? (
          <Empty>{t("live.sources.imports_none")}</Empty>
        ) : (
          <div className="flex flex-col gap-3">
            {data.imports.map((imp) => (
              <Panel key={imp.run.id} className="flex flex-col gap-2 p-4" >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span className="font-medium text-foreground-strong">
                    {hasKey(lang, `live.sources.import_kind.${imp.kind}`)
                      ? t(`live.sources.import_kind.${imp.kind}`)
                      : imp.kind}
                  </span>
                  {status(imp.run)}
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
                  <span className="tabular-nums">{dateTime(locale, imp.run.started_ms)}</span>
                  <span className="font-mono">{t("live.sources.digest", { digest: imp.file_digest })}</span>
                  {imp.rows !== null ? <span>{t("live.sources.rows", { n: imp.rows })}</span> : null}
                  <span>
                    {t("live.sources.changes_value", {
                      inserted: imp.run.inserted,
                      updated: imp.run.updated,
                      unchanged: imp.run.unchanged,
                    })}
                  </span>
                  {imp.pnl_basis ? (
                    <span>
                      {t("live.sources.basis_value", {
                        basis: t(`live.recon.basis_name.${imp.pnl_basis}`),
                        how: t(`live.sources.how.${imp.pnl_basis_source ?? "declared"}`),
                      })}
                    </span>
                  ) : null}
                </div>
                {imp.issues.length === 0 ? (
                  <span className="text-sm text-muted-foreground">{t("live.sources.issues_none")}</span>
                ) : (
                  <ul className="flex flex-col gap-1 text-sm">
                    {imp.issues.slice(0, 8).map((i, k) => (
                      <li key={k} className="flex flex-wrap gap-2">
                        <span
                          className={
                            i.severity === "error"
                              ? "text-destructive"
                              : i.severity === "warning"
                                ? "text-warning"
                                : "text-muted-foreground"
                          }
                        >
                          {issueLabel(i.code)}
                        </span>
                        <span className="text-muted-foreground">
                          {i.line !== null
                            ? t("live.sources.line", { line: i.line })
                            : t("live.sources.file_level")}
                          {i.field ? ` · ${i.field}` : ""}
                        </span>
                      </li>
                    ))}
                    {imp.issues.length > 8 ? (
                      <li className="text-muted-foreground">
                        {t("live.sources.issue_more", { n: imp.issues.length - 8 })}
                      </li>
                    ) : null}
                  </ul>
                )}
              </Panel>
            ))}
          </div>
        )}
      </Section>

      <Section title={t("live.sources.runs")}>
        {data.runs.length === 0 ? (
          <Empty>{t("live.sources.runs_none")}</Empty>
        ) : (
          <ScrollTable label={t("live.sources.runs")} minWidth={940}>
            <TableHead columns={runColumns} />
            {data.runs.map((r) => (
              <TableRow key={r.id} columns={runColumns}>
                <Cell muted className="tabular-nums">
                  {dateTime(locale, r.started_ms)}
                </Cell>
                <Cell>{t(`live.sources.kind.${r.kind}`)}</Cell>
                <Cell className="font-mono text-xs">{r.source}</Cell>
                <Cell>{status(r)}</Cell>
                <Cell className="tabular-nums text-sm">
                  {t("live.sources.changes_value", {
                    inserted: r.inserted,
                    updated: r.updated,
                    unchanged: r.unchanged,
                  })}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {r.rejected}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {r.requests}
                </Cell>
                <Cell muted>{r.error_code ? errorLabel(r.error_code) : "—"}</Cell>
              </TableRow>
            ))}
          </ScrollTable>
        )}
      </Section>
    </div>
  );
}
