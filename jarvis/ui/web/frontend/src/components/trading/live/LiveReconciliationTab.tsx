/** LIVE reconciliation: every difference the venue-internal, journal and balance checks found. */
import { useState } from "react";
import {
  Cell,
  type Column,
  FactRows,
  Panel,
  SegmentedFilter,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { useLiveReconciliation } from "@/hooks/useTrading";
import { cn } from "@/lib/utils";
import { Empty, Section } from "../parts";
import { coin, dateTime, toneClass } from "../tradingFormat";
import { hasKey, type TradingLanguage, type TradingT } from "../tradingI18n";
import { LiveSourceNotice, SeverityBadge } from "./liveParts";

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
  const columns: Column[] = [
    { id: "severity", label: t("live.recon.col.severity"), width: "80px" },
    { id: "check", label: t("live.recon.col.check"), width: "minmax(110px, 0.8fr)" },
    { id: "finding", label: t("live.recon.col.finding"), width: "minmax(200px, 2fr)" },
    { id: "market", label: t("live.recon.col.market"), width: "minmax(80px, 0.6fr)" },
    { id: "ids", label: t("live.recon.col.ids"), width: "minmax(130px, 1fr)" },
    { id: "venue", label: t("live.recon.col.venue"), align: "right" },
    { id: "other", label: t("live.recon.col.other"), align: "right" },
    { id: "diff", label: t("live.recon.col.diff"), align: "right" },
  ];
  const basis = data.pnl_basis
    ? hasKey(lang, `live.recon.basis_name.${data.pnl_basis}`)
      ? t(`live.recon.basis_name.${data.pnl_basis}`)
      : data.pnl_basis
    : null;

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
              ...(basis ? [{ label: "P/L", value: t("live.recon.basis", { basis }) }] : []),
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
          <Table label={t("live.tabs.reconciliation")}>
            <TableHead columns={columns} />
            {data.findings.map((f, i) => (
              <TableRow key={`${f.code}:${f.position_id ?? ""}:${f.entry_id ?? ""}:${i}`} columns={columns}>
                <Cell>
                  <SeverityBadge severity={f.severity} t={t} />
                </Cell>
                <Cell>{t(`live.recon.group.${f.group}`)}</Cell>
                <Cell>
                  <div className="text-sm text-foreground">
                    {hasKey(lang, `live.recon.code.${f.code}`) ? t(`live.recon.code.${f.code}`) : f.code}
                  </div>
                  {f.ts_ms ? (
                    <div className="text-xs text-muted-foreground">{dateTime(locale, f.ts_ms)}</div>
                  ) : null}
                </Cell>
                <Cell>{f.symbol ?? "—"}</Cell>
                <Cell className="font-mono text-xs text-foreground-faint">
                  {[f.position_id, f.entry_id].filter(Boolean).join(" · ") || "—"}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {amount(f.venue_value)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {amount(f.other_value)}
                </Cell>
                <Cell align="right" className={cn("tabular-nums font-medium", toneClass(f.difference))}>
                  {amount(f.difference, true)}
                </Cell>
              </TableRow>
            ))}
          </Table>
        )}
      </Section>
    </div>
  );
}
