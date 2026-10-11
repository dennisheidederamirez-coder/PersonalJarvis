/** LIVE trades: every archived fill, newest first, with its fee and realized P/L. */
import { useState } from "react";
import {
  Cell,
  type Column,
  SegmentedFilter,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { useLiveTrades } from "@/hooks/useTrading";
import { cn } from "@/lib/utils";
import { Empty, Section } from "../parts";
import { coin, dateTime, price, qty, toneClass } from "../tradingFormat";
import type { TradingT } from "../tradingI18n";
import { LiveSourceNotice, OriginTag, ScrollTable } from "./liveParts";

const PAGE = 100;

export function LiveTradesTab({ locale, t }: { locale: string; t: TradingT }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const { data } = useLiveTrades({ symbol, limit });
  const amount = (v: number | null | undefined, signed = false) =>
    coin(locale, v, { signed, digits: 4 });
  const columns: Column[] = [
    { id: "time", label: t("live.trades.time"), width: "minmax(100px, 0.8fr)" },
    { id: "symbol", label: t("strategy.market"), width: "minmax(80px, 0.6fr)" },
    { id: "side", label: t("live.trades.side"), width: "80px" },
    { id: "qty", label: t("live.trades.qty"), align: "right", width: "minmax(72px, 1fr)" },
    { id: "price", label: t("live.trades.price"), align: "right", width: "minmax(80px, 1fr)" },
    { id: "fee", label: t("live.trades.fee"), align: "right", width: "minmax(96px, 1fr)" },
    { id: "realized", label: t("live.trades.realized"), align: "right", width: "minmax(112px, 1fr)" },
    { id: "role", label: t("live.trades.role"), width: "70px" },
    { id: "ids", label: t("live.trades.position"), width: "minmax(150px, 1fr)" },
    { id: "origin", label: t("live.positions.origin"), width: "60px" },
  ];
  if (!data) return null;
  return (
    <div className="flex flex-col gap-6" data-testid="live-trades">
      <LiveSourceNotice source={data.source} t={t} />
      {data.symbols.length > 1 ? (
        <SegmentedFilter<string>
          label={t("live.positions.filter")}
          value={symbol ?? "*"}
          onChange={(v) => {
            setSymbol(v === "*" ? null : v);
            setLimit(PAGE);
          }}
          options={[
            { id: "*", label: t("live.positions.all") },
            ...data.symbols.map((s) => ({ id: s, label: s })),
          ]}
        />
      ) : null}
      <Section
        title={t("live.trades.title")}
        actions={
          <span className="text-sm tabular-nums text-muted-foreground">
            {t("live.trades.count", { shown: data.fills.length, total: data.total })}
          </span>
        }
      >
        {data.fills.length === 0 ? (
          <Empty>{t("live.trades.none")}</Empty>
        ) : (
          <ScrollTable label={t("live.trades.title")} minWidth={1110}>
            <TableHead columns={columns} />
            {data.fills.map((f) => (
              <TableRow key={f.trade_id} columns={columns}>
                <Cell muted className="tabular-nums">
                  {dateTime(locale, f.ts_ms)}
                </Cell>
                <Cell>{f.symbol}</Cell>
                <Cell>{f.side ? t(`live.fill_side.${f.side}`) : "—"}</Cell>
                <Cell align="right" className="tabular-nums">
                  {qty(locale, f.qty)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {price(locale, f.price)}
                </Cell>
                <Cell align="right" className="tabular-nums">
                  {amount(f.fee)}
                </Cell>
                <Cell align="right" className={cn("tabular-nums", toneClass(f.realized))}>
                  {f.realized === 0 ? "—" : amount(f.realized, true)}
                </Cell>
                <Cell>{t(`live.role.${f.role ?? "unknown"}`)}</Cell>
                <Cell className="font-mono text-xs text-foreground-faint">
                  {f.position_id ?? "—"} · {f.order_id}
                </Cell>
                <Cell>
                  <OriginTag origin={f.origin} t={t} />
                </Cell>
              </TableRow>
            ))}
          </ScrollTable>
        )}
        <p className="text-sm text-muted-foreground">
          {t("live.trades.fees_total", { total: data.total, fees: coin(locale, data.fees, { digits: 4 }) })}
        </p>
        {data.fills.length < data.total ? (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => setLimit((n) => n + PAGE)}
              className="rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-secondary"
            >
              {t("live.trades.more")}
            </button>
          </div>
        ) : null}
      </Section>
    </div>
  );
}
