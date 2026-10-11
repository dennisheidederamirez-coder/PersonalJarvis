/** LIVE fees and funding: totals, per month and per market. */
import { Coins, Percent, TrendingDown, TrendingUp } from "lucide-react";
import {
  Cell,
  type Column,
  StatGroup,
  StatTile,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import type { TradingLiveCostRow, TradingLiveCostsView } from "@/types/trading";
import { Empty, Section } from "../parts";
import { coin, pct, toneClass, utcPeriod } from "../tradingFormat";
import type { TradingT } from "../tradingI18n";

export function LiveCostsTab({
  data,
  locale,
  t,
}: {
  data: TradingLiveCostsView;
  locale: string;
  t: TradingT;
}) {
  const amount = (v: number | null | undefined, signed = false) => coin(locale, v, { signed });
  const { totals } = data;
  const paid = data.by_month.reduce((s, r) => s + r.funding_paid, 0);
  const received = data.by_month.reduce((s, r) => s + r.funding_received, 0);

  function table(rows: TradingLiveCostRow[], keyLabel: string, month: boolean) {
    const columns: Column[] = [
      { id: "key", label: keyLabel, width: "minmax(100px, 1fr)" },
      { id: "n", label: t("live.costs.positions"), align: "right", width: "90px" },
      { id: "realized", label: t("live.costs.realized"), align: "right" },
      { id: "fees", label: t("live.costs.fees"), align: "right" },
      { id: "paid", label: t("live.costs.funding_paid"), align: "right" },
      { id: "received", label: t("live.costs.funding_received"), align: "right" },
      { id: "net", label: t("live.costs.net"), align: "right" },
      { id: "share", label: t("live.costs.fee_share"), align: "right", width: "110px" },
    ];
    return (
      <Table label={keyLabel}>
        <TableHead columns={columns} />
        {rows.map((r) => (
          <TableRow key={r.key} columns={columns}>
            <Cell>{month ? utcPeriod(locale, r.key) : r.key}</Cell>
            <Cell align="right" className="tabular-nums">
              {r.positions}
            </Cell>
            <Cell align="right" className={cn("tabular-nums", toneClass(r.realized))}>
              {amount(r.realized, true)}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {amount(r.fees)}
            </Cell>
            <Cell align="right" className={cn("tabular-nums", toneClass(r.funding_paid))}>
              {amount(r.funding_paid, true)}
            </Cell>
            <Cell align="right" className={cn("tabular-nums", toneClass(r.funding_received))}>
              {amount(r.funding_received, true)}
            </Cell>
            <Cell align="right" className={cn("tabular-nums font-medium", toneClass(r.net))}>
              {amount(r.net, true)}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {pct(locale, r.fee_share, { digits: 1 })}
            </Cell>
          </TableRow>
        ))}
      </Table>
    );
  }

  return (
    <div className="flex flex-col gap-6" data-testid="live-costs">
      <StatGroup>
        <StatTile
          cell
          icon={<Coins className="h-4 w-4" />}
          label={t("live.costs.fees")}
          value={amount(totals.fees)}
          hint={t("live.costs.fees_hint", {
            maker: data.maker_fills,
            taker: data.taker_fills,
            unknown: data.unknown_role_fills,
          })}
        />
        <StatTile
          cell
          icon={<TrendingDown className="h-4 w-4" />}
          label={t("live.costs.funding_paid")}
          value={<span className={toneClass(paid)}>{amount(paid, true)}</span>}
        />
        <StatTile
          cell
          icon={<TrendingUp className="h-4 w-4" />}
          label={t("live.costs.funding_received")}
          value={<span className={toneClass(received)}>{amount(received, true)}</span>}
          hint={`${t("live.costs.funding_net")}: ${amount(totals.funding, true)}`}
        />
        <StatTile
          cell
          icon={<Percent className="h-4 w-4" />}
          label={t("live.costs.fee_share")}
          value={pct(locale, data.fee_share, { digits: 1 })}
          hint={t("live.costs.fee_share_hint")}
        />
      </StatGroup>
      <Section title={t("live.costs.by_month")}>
        {data.by_month.length === 0 ? (
          <Empty>{t("live.costs.none")}</Empty>
        ) : (
          table(data.by_month, t("live.costs.month"), true)
        )}
      </Section>
      <Section title={t("live.costs.by_symbol")}>
        {data.by_symbol.length === 0 ? (
          <Empty>{t("live.costs.none")}</Empty>
        ) : (
          table(data.by_symbol, t("live.costs.market"), false)
        )}
      </Section>
    </div>
  );
}
