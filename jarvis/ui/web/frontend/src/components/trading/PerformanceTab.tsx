/** Performance per strategy × market, per account and per market, plus the latest trades. */
import {
  Cell,
  type Column,
  Panel,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import type { TradingPerfRow, TradingPerformanceView } from "@/types/trading";
import { Empty, ReasonText, Section, SideBadge } from "./parts";
import { TradingEquityChart } from "./TradingEquityChart";
import { dateTime, money, num, pct, price, signedR, toneClass } from "./tradingFormat";
import type { TradingLanguage, TradingT } from "./tradingI18n";

/** The profit factor is capped at 99 when there is no losing trade. */
const PF_CAP = 99;

function PerfTable({
  rows,
  title,
  locale,
  t,
  label,
}: {
  rows: TradingPerfRow[];
  title: string;
  locale: string;
  t: TradingT;
  label: (r: TradingPerfRow) => string;
}) {
  const columns: Column[] = [
    { id: "key", label: t("performance.key"), width: "minmax(230px, 2fr)" },
    { id: "trades", label: t("performance.trades"), align: "right", width: "70px" },
    { id: "win", label: t("performance.win_rate"), align: "right" },
    { id: "pf", label: t("performance.pf"), align: "right" },
    { id: "r", label: t("performance.avg_r"), align: "right" },
    { id: "net", label: t("performance.net"), align: "right" },
    { id: "fees", label: t("performance.fees"), align: "right" },
    { id: "bw", label: t("performance.best_worst"), align: "right", width: "minmax(150px, 1.2fr)" },
    { id: "hold", label: t("performance.hold"), align: "right" },
  ];
  return (
    <Section title={title}>
      <Table label={title}>
        <TableHead columns={columns} />
        {rows.map((r) => (
          <TableRow key={`${r.candidate}:${r.key}`} columns={columns}>
            <Cell className="truncate">{label(r)}</Cell>
            <Cell align="right" className="tabular-nums">
              {r.trades}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {pct(locale, r.win_rate, { digits: 0 })}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {r.profit_factor === null
                ? "—"
                : r.profit_factor >= PF_CAP
                  ? t("performance.pf_capped")
                  : num(locale, r.profit_factor, 2)}
            </Cell>
            <Cell align="right" className={cn("tabular-nums", toneClass(r.avg_r))}>
              {signedR(locale, r.avg_r)}
            </Cell>
            <Cell align="right" className={cn("tabular-nums", toneClass(r.net))}>
              {money(locale, r.net, true)}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {money(locale, r.fees)}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {r.best === null ? "—" : `${money(locale, r.best, true)} / ${money(locale, r.worst, true)}`}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {r.avg_hold_h === null ? "—" : t("performance.hours", { h: num(locale, r.avg_hold_h, 1) })}
            </Cell>
          </TableRow>
        ))}
      </Table>
    </Section>
  );
}

export function PerformanceTab({
  data,
  locale,
  lang,
  t,
  capital,
}: {
  data: TradingPerformanceView;
  locale: string;
  lang: TradingLanguage;
  t: TradingT;
  capital: number;
}) {
  const c = data.criteria;
  const evaluable = data.by_candidate.some((r) => r.trades >= c.min_trades);
  const tradeColumns: Column[] = [
    { id: "exit", label: t("decisions.time"), width: "minmax(100px, 0.8fr)" },
    { id: "stream", label: t("decisions.stream"), width: "minmax(100px, 0.8fr)" },
    { id: "side", label: t("positions.side"), width: "70px" },
    { id: "prices", label: t("performance.entry_exit"), width: "minmax(150px, 1.1fr)" },
    { id: "reason", label: t("performance.exit_reason"), width: "minmax(160px, 1.4fr)" },
    { id: "net", label: t("performance.net"), align: "right" },
    { id: "r", label: t("performance.r"), align: "right", width: "70px" },
  ];
  return (
    <div className="flex flex-col gap-6" data-testid="trading-performance">
      <Panel className="flex flex-col gap-1 p-4">
        <span className="text-sm font-medium text-foreground-strong">
          {t("performance.criteria_title")}
        </span>
        <span className="text-sm text-muted-foreground">
          {t("performance.criteria", {
            pf: num(locale, c.profit_factor_min, 2),
            r: num(locale, c.avg_r_min, 2),
            dd: pct(locale, c.max_drawdown_max, { digits: 0 }),
            n: c.min_trades,
          })}
        </span>
        {!evaluable ? (
          <span className="text-sm text-warning">{t("performance.not_evaluable")}</span>
        ) : null}
      </Panel>

      <Panel className="p-4">
        <TradingEquityChart series={data.equity} capital={capital} locale={locale} t={t} />
      </Panel>

      <PerfTable
        rows={data.by_stream}
        title={t("performance.by_stream")}
        locale={locale}
        t={t}
        label={(r) => `${r.key} · ${r.strategy ? t(`strategy.${r.strategy}`) : ""}`}
      />
      <PerfTable
        rows={data.by_candidate}
        title={t("performance.by_candidate")}
        locale={locale}
        t={t}
        label={(r) => t("account.name", { id: r.key })}
      />
      <PerfTable
        rows={data.by_symbol}
        title={t("performance.by_symbol")}
        locale={locale}
        t={t}
        label={(r) => r.key}
      />

      <Section title={t("performance.trades_title")}>
        {data.trades.length === 0 ? (
          <Empty>{t("performance.no_trades")}</Empty>
        ) : (
          <Table label={t("performance.trades_title")}>
            <TableHead columns={tradeColumns} />
            {data.trades.map((tr) => (
              <TableRow key={tr.id} columns={tradeColumns}>
                <Cell muted className="tabular-nums">
                  {dateTime(locale, tr.exit_ms)}
                </Cell>
                <Cell className="font-mono text-xs">{tr.stream ?? tr.symbol}</Cell>
                <Cell>
                  <SideBadge side={tr.side} t={t} />
                </Cell>
                <Cell className="tabular-nums">
                  {price(locale, tr.entry)} → {price(locale, tr.exit)}
                </Cell>
                <Cell>
                  <ReasonText code={tr.exit_code} text={tr.exit_reason} lang={lang} t={t} />
                </Cell>
                <Cell align="right" className={cn("tabular-nums", toneClass(tr.net))}>
                  {money(locale, tr.net, true)}
                </Cell>
                <Cell align="right" className={cn("tabular-nums", toneClass(tr.r_multiple))}>
                  {signedR(locale, tr.r_multiple)}
                </Cell>
              </TableRow>
            ))}
          </Table>
        )}
      </Section>
    </div>
  );
}
