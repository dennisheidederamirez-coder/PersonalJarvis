/**
 * Overview: the 14-day test, the headline numbers, both virtual accounts and
 * their value over time.
 */
import { Activity, CalendarClock, Scale, ShieldAlert, Wallet } from "lucide-react";
import {
  Cell,
  type Column,
  FactRows,
  Panel,
  StatGroup,
  StatTile,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import type { TradingOverview, TradingPerformanceView } from "@/types/trading";
import { Dot, Meter, Section } from "./parts";
import { TradingEquityChart } from "./TradingEquityChart";
import { dateTime, money, pct, relative, toneClass } from "./tradingFormat";
import type { TradingT } from "./tradingI18n";

const TEST_TONE = {
  running: "ok",
  not_enabled: "off",
  finished: "off",
  disabled: "warn",
  refused: "error",
} as const;

export function OverviewTab({
  data,
  performance,
  locale,
  t,
  now,
}: {
  data: TradingOverview;
  performance: TradingPerformanceView | undefined;
  locale: string;
  t: TradingT;
  now: number;
}) {
  const { test, combined, accounts } = data;
  const trades = accounts.reduce((n, a) => n + a.trades, 0);
  const minTrades = accounts[0]?.min_trades ?? 0;
  const columns: Column[] = [
    { id: "name", label: t("positions.account"), width: "minmax(260px, 2fr)" },
    { id: "equity", label: t("account.equity"), align: "right" },
    { id: "return", label: t("account.return"), align: "right" },
    { id: "realized", label: t("account.realized"), align: "right" },
    { id: "unrealized", label: t("account.unrealized"), align: "right" },
    { id: "fees", label: t("account.fees"), align: "right" },
    { id: "dd", label: t("account.drawdown"), align: "right", width: "minmax(170px, 1.3fr)" },
    { id: "trades", label: t("account.trades"), align: "right", width: "80px" },
    { id: "state", label: t("strategy.status"), width: "minmax(140px, 1.2fr)" },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div data-testid="trading-test-status">
      <Panel className="flex flex-col gap-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <CalendarClock className="h-5 w-5 text-muted-foreground" aria-hidden />
            <h3 className="text-base font-semibold text-foreground-strong">{t("test.title")}</h3>
            <Dot tone={TEST_TONE[test.state]} label={t(`test.state.${test.state}`)} />
          </div>
          {test.days_total > 0 ? (
            <span className="text-sm tabular-nums text-muted-foreground">
              {t("test.day", { day: test.day, total: test.days_total })}
            </span>
          ) : null}
        </div>
        {test.days_total > 0 ? (
          <Meter ratio={test.progress} label={t("test.title")} />
        ) : null}
        <FactRows
          rows={[
            {
              label: t("test.window"),
              value:
                test.from_ms !== null
                  ? `${dateTime(locale, test.from_ms)} – ${dateTime(locale, test.until_ms)}`
                  : t("test.none"),
            },
            {
              label: t("test.runs"),
              value: (
                <span className="tabular-nums">
                  {t("test.runs_value", { done: test.runs_done, expected: test.runs_expected })}
                  {test.runs_missed > 0 ? (
                    <span className="ml-2 text-warning">
                      {t("test.missed", { n: test.runs_missed })}
                    </span>
                  ) : null}
                </span>
              ),
            },
            {
              label: t("test.last_run"),
              value: test.last_run_ms
                ? `${dateTime(locale, test.last_run_ms)} (${relative(locale, test.last_run_ms, now)})`
                : t("test.none"),
            },
            {
              label: t("test.next_run"),
              value: test.next_run_ms
                ? `${dateTime(locale, test.next_run_ms)} (${relative(locale, test.next_run_ms, now)})`
                : t("test.none"),
            },
            {
              label: t("test.spec"),
              value: (
                <span className="font-mono text-sm">
                  {test.spec_name} · {test.spec_digest}
                </span>
              ),
            },
            {
              label: t("test.code"),
              value: <span className="font-mono text-sm">{test.code_digest || t("test.none")}</span>,
            },
          ]}
        />
      </Panel>
      </div>

      <StatGroup>
        <StatTile
          cell
          icon={<Wallet className="h-4 w-4" />}
          label={t("overview.equity")}
          value={money(locale, combined.equity)}
          hint={t("overview.equity_hint", { capital: money(locale, combined.capital) })}
        />
        <StatTile
          cell
          icon={<Activity className="h-4 w-4" />}
          label={t("overview.pnl")}
          value={
            <span className={toneClass(combined.equity - combined.capital)}>
              {money(locale, combined.equity - combined.capital, true)}{" "}
              <span className="text-base font-normal">
                ({pct(locale, combined.return_pct, { signed: true })})
              </span>
            </span>
          }
          hint={t("overview.pnl_hint", {
            realized: money(locale, combined.realized, true),
            unrealized: money(locale, combined.unrealized, true),
          })}
        />
        <StatTile
          cell
          icon={<ShieldAlert className="h-4 w-4" />}
          label={t("overview.open_risk")}
          value={money(locale, combined.open_risk)}
          hint={t("overview.open_risk_hint")}
        />
        <StatTile
          cell
          icon={<Scale className="h-4 w-4" />}
          label={t("overview.trades")}
          value={String(trades)}
          hint={t("overview.trades_hint", { min: minTrades })}
        />
      </StatGroup>

      <Section title={t("overview.accounts")} testId="trading-accounts">
        <Table label={t("overview.accounts")}>
          <TableHead columns={columns} />
          {accounts.map((a) => (
            <TableRow key={a.candidate} columns={columns}>
              <Cell>
                <div className="truncate font-medium text-foreground">
                  {t("account.name", { id: a.candidate })}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {t(`account.strategy_${a.candidate}`)}
                </div>
              </Cell>
              <Cell align="right" className="tabular-nums">
                {money(locale, a.equity)}
              </Cell>
              <Cell align="right" className={cn("tabular-nums", toneClass(a.return_pct))}>
                {pct(locale, a.return_pct, { signed: true })}
              </Cell>
              <Cell align="right" className={cn("tabular-nums", toneClass(a.realized))}>
                {money(locale, a.realized, true)}
              </Cell>
              <Cell align="right" className={cn("tabular-nums", toneClass(a.unrealized))}>
                {money(locale, a.unrealized, true)}
              </Cell>
              <Cell align="right" className="tabular-nums">
                {money(locale, a.fees)}
              </Cell>
              <Cell align="right" className="tabular-nums">
                {t("account.drawdown_value", {
                  now: pct(locale, a.drawdown_now),
                  max: pct(locale, a.max_drawdown),
                })}
              </Cell>
              <Cell align="right" className="tabular-nums">
                {t("account.trades_value", { n: a.trades, min: a.min_trades })}
              </Cell>
              <Cell>
                {a.kill_switch ? (
                  <Dot tone="error" label={t("account.kill_switch", { reason: a.kill_reason })} />
                ) : a.halted_day ? (
                  <Dot tone="warn" label={t("account.halted", { day: a.halted_day })} />
                ) : (
                  <Dot tone="ok" label={t("account.ok")} />
                )}
              </Cell>
            </TableRow>
          ))}
        </Table>
        {combined.same_symbol_overlap.length > 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("overview.overlap", { symbols: combined.same_symbol_overlap.join(", ") })}
          </p>
        ) : null}
      </Section>

      <Section title={t("overview.equity_chart")}>
        <Panel className="p-4">
          <TradingEquityChart
            series={performance?.equity ?? []}
            capital={accounts[0]?.capital ?? 0}
            locale={locale}
            t={t}
          />
        </Panel>
      </Section>
    </div>
  );
}
