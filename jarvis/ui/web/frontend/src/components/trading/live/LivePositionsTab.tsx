/** LIVE positions: open ones, then closed ones newest first, with fees, funding and net. */
import { useState } from "react";
import {
  Cell,
  type Column,
  SegmentedFilter,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { useLivePositions } from "@/hooks/useTrading";
import { cn } from "@/lib/utils";
import type { TradingLivePosition } from "@/types/trading";
import { Empty, Section, SideBadge } from "../parts";
import { coin, dateTime, price, qty, toneClass } from "../tradingFormat";
import type { TradingT } from "../tradingI18n";
import { LiveSourceNotice, OriginTag } from "./liveParts";

const PAGE = 50;

export function LivePositionsTab({ locale, t }: { locale: string; t: TradingT }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const { data } = useLivePositions({ symbol, limit });
  const amount = (v: number | null | undefined, signed = false) =>
    coin(locale, v, { signed, digits: 4 });

  const columns: Column[] = [
    { id: "symbol", label: t("strategy.market"), width: "minmax(90px, 0.8fr)" },
    { id: "side", label: t("live.positions.side"), width: "70px" },
    { id: "qty", label: t("live.positions.qty"), align: "right" },
    { id: "entry", label: t("live.positions.entry"), align: "right" },
    { id: "close", label: t("live.positions.close"), align: "right" },
    { id: "realized", label: t("live.positions.realized"), align: "right" },
    { id: "fee", label: t("live.positions.fee"), align: "right" },
    { id: "funding", label: t("live.positions.funding"), align: "right" },
    { id: "net", label: t("live.positions.net"), align: "right" },
    { id: "time", label: t("live.positions.closed_at"), width: "minmax(100px, 0.8fr)" },
    { id: "origin", label: t("live.positions.origin"), width: "60px" },
    { id: "findings", label: t("live.positions.findings"), width: "minmax(140px, 1.2fr)" },
  ];
  const openColumns: Column[] = [
    ...columns.slice(0, 4),
    { id: "unrealized", label: t("live.positions.unrealized"), align: "right" },
    ...columns.slice(6, 8),
    { id: "time", label: t("live.positions.opened"), width: "minmax(100px, 0.8fr)" },
    columns[10],
  ];

  function row(p: TradingLivePosition, open: boolean) {
    return (
      <TableRow key={p.position_id} columns={open ? openColumns : columns}>
        <Cell>
          <div className="truncate">{p.symbol}</div>
          <div className="truncate font-mono text-xs text-foreground-faint">{p.position_id}</div>
        </Cell>
        <Cell>
          <SideBadge side={p.side} t={t} />
        </Cell>
        <Cell align="right" className="tabular-nums">
          {qty(locale, p.qty)}
        </Cell>
        <Cell align="right" className="tabular-nums">
          {price(locale, p.entry_price)}
        </Cell>
        {open ? (
          <Cell align="right" className={cn("tabular-nums", toneClass(p.unrealized))}>
            {amount(p.unrealized, true)}
          </Cell>
        ) : (
          <>
            <Cell align="right" className="tabular-nums">
              {price(locale, p.close_price)}
            </Cell>
            <Cell align="right" className={cn("tabular-nums", toneClass(p.realized))}>
              {amount(p.realized, true)}
            </Cell>
          </>
        )}
        <Cell align="right" className="tabular-nums">
          {amount(p.fee)}
        </Cell>
        <Cell align="right" className={cn("tabular-nums", toneClass(p.funding))}>
          {amount(p.funding, true)}
        </Cell>
        {open ? null : (
          <Cell align="right" className={cn("tabular-nums font-medium", toneClass(p.net))}>
            {amount(p.net, true)}
          </Cell>
        )}
        <Cell muted className="tabular-nums">
          {dateTime(locale, open ? p.opened_ms : p.closed_ms)}
        </Cell>
        <Cell>
          <OriginTag origin={p.origin} t={t} />
        </Cell>
        {open ? null : (
          <Cell>
            {p.findings.length ? (
              <span className="text-sm text-warning">
                {p.findings.map((c) => t(`live.recon.code.${c}`)).join(", ")}
              </span>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </Cell>
        )}
      </TableRow>
    );
  }

  if (!data) return null;
  return (
    <div className="flex flex-col gap-6" data-testid="live-positions">
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

      <Section title={t("live.positions.open_title")}>
        {data.open.length === 0 ? (
          <Empty>{t("live.positions.open_none")}</Empty>
        ) : (
          <Table label={t("live.positions.open_title")}>
            <TableHead columns={openColumns} />
            {data.open.map((p) => row(p, true))}
          </Table>
        )}
      </Section>

      <Section
        title={t("live.positions.closed_title")}
        actions={
          <span className="text-sm tabular-nums text-muted-foreground">
            {t("live.positions.count", { shown: data.closed.length, total: data.total_closed })}
          </span>
        }
      >
        {data.closed.length === 0 ? (
          <Empty>{t("live.positions.closed_none")}</Empty>
        ) : (
          <Table label={t("live.positions.closed_title")}>
            <TableHead columns={columns} />
            {data.closed.map((p) => row(p, false))}
          </Table>
        )}
        {data.closed.length < data.total_closed ? (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => setLimit((n) => n + PAGE)}
              className="rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-secondary"
            >
              {t("live.positions.more")}
            </button>
          </div>
        ) : null}
      </Section>
    </div>
  );
}
