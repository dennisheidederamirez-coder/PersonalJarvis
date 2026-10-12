/**
 * LIVE reconciliation: every difference the venue-internal, journal and
 * balance checks found — each one tied to the position, journal entry, fills
 * or balance observations it is about, with both values, their sources, the
 * difference and the likely cause in words.
 */
import { useState } from "react";
import {
  Cell,
  type Column,
  FactRows,
  Panel,
  SegmentedFilter,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { useLiveReconciliation } from "@/hooks/useTrading";
import { cn } from "@/lib/utils";
import type { TradingLiveFinding } from "@/types/trading";
import { Empty, Section, SideBadge } from "../parts";
import { coin, dateTime, toneClass } from "../tradingFormat";
import { hasKey, type TradingLanguage, type TradingT } from "../tradingI18n";
import { isDerivedId, LiveSourceNotice, OriginTag, ScrollTable, SeverityBadge } from "./liveParts";

export function LiveReconciliationTab({
  locale,
  lang,
  t,
}: {
  locale: string;
  lang: TradingLanguage;
  t: TradingT;
}) {
  const [journal, setJournal] = useState<string | null>(null);
  const { data } = useLiveReconciliation(journal);
  if (!data) return null;
  const amount = (v: number | null | undefined, signed = false) =>
    coin(locale, v, { signed, digits: 4 });
  const label = (key: string, fallback: string) => (hasKey(lang, key) ? t(key) : fallback);
  const basisName = (b: string | null) =>
    b ? label(`live.recon.basis_name.${b}`, b) : t("live.recon.ref.none");
  const columns: Column[] = [
    { id: "severity", label: t("live.recon.col.severity"), width: "80px" },
    { id: "finding", label: t("live.recon.col.finding"), width: "minmax(260px, 2.4fr)" },
    { id: "assignment", label: t("live.recon.col.assignment"), width: "minmax(200px, 1.4fr)" },
    { id: "venue", label: t("live.recon.col.venue"), align: "right", width: "minmax(130px, 0.9fr)" },
    { id: "other", label: t("live.recon.col.other"), align: "right", width: "minmax(150px, 1fr)" },
    { id: "diff", label: t("live.recon.col.diff"), align: "right", width: "minmax(110px, 0.8fr)" },
  ];

  function assignment(f: TradingLiveFinding) {
    const lines: string[] = [];
    if (f.position_id) {
      lines.push(
        isDerivedId(f.position_id)
          ? t("live.recon.ref.position_derived", { id: f.position_id.slice(2, 10) })
          : t("live.recon.ref.position", { id: f.position_id }),
      );
    } else if (f.group === "totals") {
      lines.push(t("live.recon.ref.no_position"));
    }
    if (f.entry_id) lines.push(t("live.recon.ref.entry", { id: f.entry_id }));
    if (f.fill_ids.length) lines.push(t("live.recon.ref.fills", { ids: f.fill_ids.join(", ") }));
    const times: string[] = [];
    if (f.period_from_ms !== null && f.period_to_ms !== null) {
      times.push(
        t("live.recon.ref.period", {
          from: dateTime(locale, f.period_from_ms),
          to: dateTime(locale, f.period_to_ms),
        }),
      );
    } else {
      if (f.opened_ms !== null) times.push(t("live.recon.ref.opened", { time: dateTime(locale, f.opened_ms) }));
      if (f.closed_ms !== null) times.push(t("live.recon.ref.closed", { time: dateTime(locale, f.closed_ms) }));
    }
    return (
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate">{f.symbol ?? "—"}</span>
          {f.side ? <SideBadge side={f.side} t={t} /> : null}
        </div>
        {lines.map((l) => (
          <span key={l} className="break-words font-mono text-xs text-foreground-faint">
            {l}
          </span>
        ))}
        {times.length ? (
          <span className="text-xs text-muted-foreground">{times.join(" · ")}</span>
        ) : null}
      </div>
    );
  }

  function valueCell(value: number | null, source: React.ReactNode) {
    return (
      <div className="flex flex-col items-end gap-1">
        <span className="tabular-nums">{amount(value)}</span>
        <span className="text-right text-xs text-muted-foreground">{source}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6" data-testid="live-reconciliation">
      <LiveSourceNotice source={data.source} t={t} />
      {data.journals.length > 1 ? (
        <SegmentedFilter<string>
          label={t("live.recon.journal")}
          value={data.journal ?? ""}
          onChange={setJournal}
          options={data.journals.map((j) => ({ id: j, label: j }))}
        />
      ) : null}
      <Section title={t("live.recon.summary")}>
        <Panel className="flex flex-col gap-3 p-5">
          <FactRows
            rows={[
              {
                label: t("live.recon.journal"),
                value:
                  data.journal && data.journal_from_ms !== null ? (
                    <span>
                      <span className="font-mono">{data.journal}</span> ·{" "}
                      {t("live.recon.span", {
                        from: dateTime(locale, data.journal_from_ms),
                        to: dateTime(locale, data.journal_to_ms),
                      })}
                    </span>
                  ) : (
                    t("live.recon.journal_none")
                  ),
              },
              ...(data.pnl_basis
                ? [{ label: "P/L", value: t("live.recon.basis", { basis: basisName(data.pnl_basis) }) }]
                : []),
              {
                label: t("live.recon.summary"),
                value: t("live.recon.compared", {
                  positions: data.positions_compared,
                  entries: data.entries_compared,
                  matched: data.matched,
                  checks: data.balance_checks,
                }),
              },
            ]}
          />
          <p className="text-sm text-muted-foreground">{t("live.recon.control_note")}</p>
        </Panel>
      </Section>
      <Section
        title={t("live.tabs.reconciliation")}
        actions={
          <span className="text-sm tabular-nums text-muted-foreground">{data.findings.length}</span>
        }
      >
        {data.findings.length === 0 ? (
          <Empty>{t("live.recon.none")}</Empty>
        ) : (
          <ScrollTable label={t("live.tabs.reconciliation")} minWidth={1080}>
            <TableHead columns={columns} />
            {data.findings.map((f) => (
              <TableRow key={f.id} id={`finding-${f.id}`} columns={columns} className="items-start py-3">
                <Cell>
                  <SeverityBadge severity={f.severity} t={t} />
                </Cell>
                <Cell>
                  <div className="flex flex-col gap-1" data-testid="live-finding">
                    <span className="text-sm font-medium text-foreground">
                      {label(`live.recon.code.${f.code}`, f.code)}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {t(`live.recon.group.${f.group}`)}
                      {f.field ? ` · ${label(`live.recon.field.${f.field}`, f.field)}` : ""}
                    </span>
                    <span className="whitespace-normal text-sm text-muted-foreground">
                      {label(`live.recon.cause.${f.code}`, "")
                        .replace("{basis}", basisName(f.basis))}
                    </span>
                  </div>
                </Cell>
                <Cell>{assignment(f)}</Cell>
                <Cell align="right">
                  {f.venue_value === null && f.other_value === null
                    ? "—"
                    : valueCell(
                        f.venue_value,
                        f.venue_source ? <OriginTag origin={f.venue_source} t={t} /> : null,
                      )}
                </Cell>
                <Cell align="right">
                  {f.venue_value === null && f.other_value === null
                    ? "—"
                    : valueCell(
                        f.other_value,
                        t(`live.recon.other_source.${f.other_source}`, { journal: f.journal ?? "" }),
                      )}
                </Cell>
                <Cell align="right" className={cn("tabular-nums font-medium", toneClass(f.difference))}>
                  {amount(f.difference, true)}
                </Cell>
              </TableRow>
            ))}
          </ScrollTable>
        )}
      </Section>
    </div>
  );
}
