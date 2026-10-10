/**
 * Signals & decisions: every signal, approval, rejection, fill and every
 * "no trade" with its reason — filterable by kind, strategy and reason.
 */
import { useState } from "react";
import {
  Cell,
  type Column,
  SegmentedFilter,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { type DecisionQuery, useTradingDecisions } from "@/hooks/useTrading";
import { cn } from "@/lib/utils";
import type { TradingDecision } from "@/types/trading";
import { Empty, ReasonText, Section, SideBadge, SourceNotice } from "./parts";
import { dateTime, money, price, qty, signedR, toneClass } from "./tradingFormat";
import { hasKey, type TradingLanguage, type TradingT } from "./tradingI18n";

const PAGE = 50;
const GROUPS: DecisionQuery["group"][] = ["all", "signal", "execution", "no_trade", "risk"];

function Details({ d, locale, t }: { d: TradingDecision; locale: string; t: TradingT }) {
  const parts: string[] = [];
  if (d.qty !== null) parts.push(`${t("positions.qty")} ${qty(locale, d.qty)}`);
  if (d.price !== null) parts.push(`@ ${price(locale, d.price)}`);
  if (d.stop !== null) parts.push(`${t("positions.stop")} ${price(locale, d.stop)}`);
  if (d.take_profit !== null)
    parts.push(`${t("positions.take_profit")} ${price(locale, d.take_profit)}`);
  if (d.risk !== null) parts.push(`${t("positions.risk")} ${money(locale, d.risk)}`);
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        {d.side ? <SideBadge side={d.side} t={t} /> : null}
        {d.net !== null ? (
          <span className={cn("tabular-nums text-sm", toneClass(d.net))}>
            {money(locale, d.net, true)}
            {d.r_multiple !== null ? ` · ${signedR(locale, d.r_multiple)} R` : ""}
          </span>
        ) : null}
      </div>
      {parts.length > 0 ? (
        <div className="truncate text-xs tabular-nums text-muted-foreground">{parts.join(" · ")}</div>
      ) : null}
    </div>
  );
}

export function DecisionsTab({
  locale,
  lang,
  t,
}: {
  locale: string;
  lang: TradingLanguage;
  t: TradingT;
}) {
  const [query, setQuery] = useState<DecisionQuery>({
    group: "all",
    stream: null,
    reasonCode: null,
    limit: PAGE,
  });
  const page = useTradingDecisions(query);
  const data = page.data;
  const columns: Column[] = [
    { id: "time", label: t("decisions.time"), width: "minmax(100px, 0.7fr)" },
    { id: "stream", label: t("decisions.stream"), width: "minmax(100px, 0.8fr)" },
    { id: "kind", label: t("decisions.kind_label"), width: "minmax(120px, 0.9fr)" },
    { id: "reason", label: t("decisions.reason"), width: "minmax(220px, 2.6fr)" },
    { id: "details", label: t("decisions.details"), width: "minmax(180px, 1.6fr)" },
  ];
  const set = (patch: Partial<DecisionQuery>) =>
    setQuery((q) => ({ ...q, limit: PAGE, ...patch }));

  return (
    <div className="flex flex-col gap-4" data-testid="trading-decisions">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <SegmentedFilter<DecisionQuery["group"]>
          label={t("decisions.filter_group")}
          value={query.group}
          onChange={(group) => set({ group, reasonCode: null })}
          options={GROUPS.map((g) => ({ id: g, label: t(`decisions.group.${g}`) }))}
        />
        <SegmentedFilter<string>
          label={t("decisions.filter_stream")}
          value={query.stream ?? "all"}
          onChange={(s) => set({ stream: s === "all" ? null : s })}
          options={[
            { id: "all", label: t("decisions.all_streams") },
            ...(data?.streams ?? []).map((s) => ({ id: s, label: s })),
          ]}
        />
      </div>

      {data && data.reason_counts.length > 0 ? (
        <div className="flex flex-col gap-2">
          <span className="text-sm text-muted-foreground">{t("decisions.reasons_title")}</span>
          <div className="flex flex-wrap gap-2">
            {data.reason_counts.slice(0, 10).map((c) => {
              const active = query.reasonCode === c.reason_code;
              return (
                <button
                  key={`${c.kind}:${c.reason_code}`}
                  type="button"
                  onClick={() => set({ reasonCode: active ? null : c.reason_code })}
                  aria-pressed={active}
                  className={cn(
                    "inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-sm transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    active
                      ? "border-accent bg-accent-soft text-foreground-strong"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  <span>{t(`decisions.kind.${c.kind}`)}</span>
                  <span className="text-foreground">
                    {hasKey(lang, `reason.${c.reason_code}`)
                      ? t(`reason.${c.reason_code}`)
                      : t("reason.other")}
                  </span>
                  <span className="tabular-nums text-foreground-faint">{c.count}</span>
                </button>
              );
            })}
            {query.reasonCode ? (
              <button
                type="button"
                onClick={() => set({ reasonCode: null })}
                className="text-sm text-muted-foreground underline-offset-2 hover:underline"
              >
                {t("decisions.clear_reason")}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {data ? <SourceNotice source={data.source} t={t} /> : null}

      <Section
        title={t("decisions.title")}
        actions={
          data ? (
            <span className="text-sm tabular-nums text-muted-foreground">
              {t("decisions.count", { shown: data.items.length, total: data.total })}
            </span>
          ) : null
        }
      >
        {data && data.items.length === 0 ? (
          <Empty>{t("decisions.none")}</Empty>
        ) : (
          <Table label={t("decisions.title")}>
            <TableHead columns={columns} />
            {(data?.items ?? []).map((d) => (
              <TableRow key={d.id} columns={columns}>
                <Cell muted className="tabular-nums">
                  {dateTime(locale, d.ts_ms)}
                </Cell>
                <Cell className="font-mono text-xs">{d.stream ?? d.symbol}</Cell>
                <Cell>{t(`decisions.kind.${d.kind}`)}</Cell>
                <Cell>
                  <ReasonText
                    code={d.reason_code}
                    text={d.reason}
                    lang={lang}
                    t={t}
                    signal={{ code: d.signal_code, text: d.signal_reason }}
                  />
                </Cell>
                <Cell>
                  <Details d={d} locale={locale} t={t} />
                </Cell>
              </TableRow>
            ))}
          </Table>
        )}
        {data && data.next_before_id !== null ? (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => setQuery((q) => ({ ...q, limit: q.limit + PAGE }))}
              className="rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-secondary"
            >
              {t("decisions.more")}
            </button>
          </div>
        ) : null}
      </Section>
    </div>
  );
}
