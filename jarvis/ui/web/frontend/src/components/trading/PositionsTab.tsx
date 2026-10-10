/** Positions & risk: open positions, queued orders and limit use per account. */
import {
  Cell,
  type Column,
  Panel,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import type { TradingLimitUse, TradingPositionsView } from "@/types/trading";
import { Dot, Empty, Meter, ReasonText, Section, SideBadge } from "./parts";
import { dateTime, money, pct, price, qty, toneClass } from "./tradingFormat";
import type { TradingLanguage, TradingT } from "./tradingI18n";

function limitValue(locale: string, lim: TradingLimitUse): { used: string; limit: string } {
  if (lim.key === "positions") return { used: String(lim.used), limit: String(lim.limit) };
  return { used: pct(locale, lim.used), limit: pct(locale, lim.limit, { digits: 1 }) };
}

export function PositionsTab({
  data,
  locale,
  lang,
  t,
}: {
  data: TradingPositionsView;
  locale: string;
  lang: TradingLanguage;
  t: TradingT;
}) {
  const columns: Column[] = [
    { id: "account", label: t("positions.account"), width: "70px" },
    { id: "symbol", label: t("strategy.market"), width: "minmax(90px, 0.8fr)" },
    { id: "side", label: t("positions.side"), width: "70px" },
    { id: "qty", label: t("positions.qty"), align: "right" },
    { id: "entry", label: t("positions.entry"), align: "right" },
    { id: "mark", label: t("positions.mark"), align: "right" },
    { id: "stop", label: t("positions.stop"), align: "right" },
    { id: "tp", label: t("positions.take_profit"), align: "right" },
    { id: "unrealized", label: t("positions.unrealized"), align: "right" },
    { id: "risk", label: t("positions.risk"), align: "right" },
    { id: "opened", label: t("positions.opened"), width: "minmax(100px, 0.8fr)" },
  ];
  const pendingColumns: Column[] = [
    { id: "account", label: t("positions.account"), width: "70px" },
    { id: "symbol", label: t("strategy.market"), width: "minmax(90px, 0.6fr)" },
    { id: "action", label: t("decisions.kind_label"), width: "minmax(130px, 0.8fr)" },
    { id: "side", label: t("positions.side"), width: "70px" },
    { id: "qty", label: t("positions.qty"), align: "right", width: "minmax(80px, 0.5fr)" },
    { id: "reason", label: t("decisions.reason"), width: "minmax(200px, 2fr)" },
  ];

  return (
    <div className="flex flex-col gap-6" data-testid="trading-positions">
      <Section title={t("positions.title")}>
        {data.positions.length === 0 ? (
          <Empty>{t("positions.none")}</Empty>
        ) : (
          <Table label={t("positions.title")}>
            <TableHead columns={columns} />
            {data.positions.map((p) => (
              <TableRow key={`${p.candidate}:${p.symbol}`} columns={columns}>
                <Cell>{p.candidate}</Cell>
                <Cell>{p.symbol}</Cell>
                <Cell>
                  <SideBadge side={p.side} t={t} />
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {qty(locale, p.qty)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {price(locale, p.entry)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {price(locale, p.mark)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {price(locale, p.stop)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {price(locale, p.take_profit)}
                </Cell>
                <Cell align="right" className={cn("tabular-nums", toneClass(p.unrealized))}>
                  {money(locale, p.unrealized, true)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {money(locale, p.risk_at_stop)}
                </Cell>
                <Cell muted className="tabular-nums">
                  {dateTime(locale, p.opened_ms)}
                </Cell>
              </TableRow>
            ))}
          </Table>
        )}
        <p className="text-sm text-muted-foreground">
          {t("positions.totals", {
            risk: money(locale, data.open_risk),
            notional: money(locale, data.notional),
          })}
        </p>
        {data.same_symbol_overlap.length > 0 ? (
          <p className="text-sm text-warning">
            {t("overview.overlap", { symbols: data.same_symbol_overlap.join(", ") })}
          </p>
        ) : null}
      </Section>

      <Section title={t("positions.pending_title")}>
        {data.pending.length === 0 ? (
          <Empty>{t("positions.pending_none")}</Empty>
        ) : (
          <Table label={t("positions.pending_title")}>
            <TableHead columns={pendingColumns} />
            {data.pending.map((o) => (
              <TableRow key={`${o.candidate}:${o.symbol}`} columns={pendingColumns}>
                <Cell>{o.candidate}</Cell>
                <Cell>{o.symbol}</Cell>
                <Cell>{t(`positions.action.${o.action}`)}</Cell>
                <Cell>
                  <SideBadge side={o.side} t={t} />
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {qty(locale, o.qty)}
                </Cell>
                <Cell>
                  <ReasonText code={o.reason_code} text={o.reason} lang={lang} t={t} />
                </Cell>
              </TableRow>
            ))}
          </Table>
        )}
      </Section>

      <Section title={t("positions.limits_title")}>
        <div className="grid gap-4 lg:grid-cols-2">
          {data.risk.map((r) => (
            <Panel key={r.candidate} className="flex flex-col gap-3 p-4">
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium text-foreground">
                  {t("account.name", { id: r.candidate })}
                </span>
                {r.kill_switch ? (
                  <Dot tone="error" label={t("account.kill_switch", { reason: r.kill_reason })} />
                ) : r.halted_day ? (
                  <Dot tone="warn" label={t("account.halted", { day: r.halted_day })} />
                ) : (
                  <Dot tone="ok" label={t("account.ok")} />
                )}
              </div>
              {r.limits.map((lim) => {
                const v = limitValue(locale, lim);
                return (
                  <div key={lim.key} className="flex flex-col gap-1">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t(`positions.limit.${lim.key}`)}
                      </span>
                      <span className="tabular-nums text-foreground">
                        {t("positions.used_of", v)}
                      </span>
                    </div>
                    <Meter ratio={lim.ratio} label={t(`positions.limit.${lim.key}`)} />
                  </div>
                );
              })}
            </Panel>
          ))}
        </div>
      </Section>
    </div>
  );
}
