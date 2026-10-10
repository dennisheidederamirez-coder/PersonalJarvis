/** Strategies: every pre-registered stream, its state and its latest event. */
import {
  Cell,
  type Column,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import type { TradingStrategiesView, TradingStreamState } from "@/types/trading";
import { Dot, ReasonText, SideBadge } from "./parts";
import { dateTime, money, relative, toneClass } from "./tradingFormat";
import type { TradingLanguage, TradingT } from "./tradingI18n";

const TONE: Record<TradingStreamState, "ok" | "off" | "warn" | "error" | "busy"> = {
  position_open: "busy",
  order_pending: "busy",
  waiting: "ok",
  no_signal: "ok",
  data_problem: "warn",
  kill_switch: "error",
  not_started: "off",
};

export function StrategiesTab({
  data,
  locale,
  lang,
  t,
  now,
}: {
  data: TradingStrategiesView;
  locale: string;
  lang: TradingLanguage;
  t: TradingT;
  now: number;
}) {
  const columns: Column[] = [
    { id: "strategy", label: t("strategy.name"), width: "minmax(190px, 1.4fr)" },
    { id: "market", label: t("strategy.market"), width: "minmax(90px, 0.6fr)" },
    { id: "status", label: t("strategy.status"), width: "minmax(150px, 1fr)" },
    { id: "event", label: t("strategy.last_event"), width: "minmax(220px, 2.4fr)" },
    { id: "bar", label: t("strategy.last_bar"), width: "minmax(110px, 0.8fr)" },
    { id: "next", label: t("strategy.next_close"), width: "minmax(110px, 0.8fr)" },
    { id: "trades", label: t("strategy.trades"), align: "right", width: "70px" },
    { id: "net", label: t("strategy.net"), align: "right", width: "minmax(90px, 0.7fr)" },
  ];
  return (
    <div data-testid="trading-strategies">
      <Table label={t("tabs.strategies")}>
        <TableHead columns={columns} />
        {data.streams.map((s) => (
          <TableRow key={s.id} columns={columns}>
            <Cell>
              <div className="truncate font-medium text-foreground">
                {t(`strategy.${s.strategy}`)} · {s.interval}
              </div>
              <div
                className="truncate font-mono text-xs text-foreground-faint"
                title={Object.entries(s.params)
                  .map(([k, v]) => `${k}=${String(v)}`)
                  .join(", ")}
              >
                {s.id}
              </div>
            </Cell>
            <Cell>{s.symbol}</Cell>
            <Cell>
              <div className="flex flex-col gap-1">
                <Dot tone={TONE[s.status]} label={t(`strategy.state.${s.status}`)} />
                {s.position_side ? <SideBadge side={s.position_side} t={t} /> : null}
              </div>
            </Cell>
            <Cell>
              {s.last_event ? (
                <ReasonText
                  code={s.last_event.reason_code}
                  text={s.last_event.reason}
                  lang={lang}
                  t={t}
                  signal={{ code: s.last_event.signal_code, text: s.last_event.signal_reason }}
                />
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </Cell>
            <Cell muted className="tabular-nums">
              {dateTime(locale, s.last_close_ms)}
            </Cell>
            <Cell muted className="tabular-nums">
              <div>{dateTime(locale, s.next_close_ms)}</div>
              {s.next_close_ms ? (
                <div className="text-xs text-foreground-faint">
                  {relative(locale, s.next_close_ms, now)}
                </div>
              ) : null}
            </Cell>
            <Cell align="right" className="tabular-nums">
              {s.trades}
            </Cell>
            <Cell align="right" className={cn("tabular-nums", toneClass(s.net))}>
              {money(locale, s.net, true)}
            </Cell>
          </TableRow>
        ))}
      </Table>
    </div>
  );
}
