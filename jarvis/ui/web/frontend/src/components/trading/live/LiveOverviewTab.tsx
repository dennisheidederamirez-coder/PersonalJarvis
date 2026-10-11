/** LIVE account: balance, net result, recent result, differences, and both curves. */
import { Activity, CalendarRange, Scale, Wallet } from "lucide-react";
import { FactRows, Panel, StatGroup, StatTile } from "@/components/extensions/primitives";
import type { TradingLiveOverview, TradingLiveSource } from "@/types/trading";
import { Section } from "../parts";
import { coin, dateTime, pct, relative, toneClass } from "../tradingFormat";
import type { TradingT } from "../tradingI18n";
import { LiveLineChart } from "./liveParts";

export function LiveOverviewTab({
  data,
  source,
  locale,
  t,
}: {
  data: TradingLiveOverview;
  source: TradingLiveSource;
  locale: string;
  t: TradingT;
}) {
  const { totals, totals_30d: recent, balance } = data;
  const unit = data.margin_coin ?? "USDT";
  const amount = (v: number | null | undefined, signed = false) =>
    coin(locale, v, { signed, unit });
  const now = source.read_at_ms;
  const errors = data.findings.error ?? 0;
  const warnings = data.findings.warning ?? 0;
  const infos = data.findings.info ?? 0;
  const day = (ts: number) =>
    new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit", timeZone: "UTC" }).format(
      new Date(ts),
    );

  return (
    <div className="flex flex-col gap-6" data-testid="live-overview">
      <StatGroup>
        <StatTile
          cell
          icon={<Wallet className="h-4 w-4" />}
          label={t("live.overview.wallet")}
          value={balance ? amount(balance.wallet) : t("live.overview.no_balance")}
          hint={
            balance
              ? t("live.overview.wallet_hint", {
                  available: amount(balance.available),
                  unrealized: amount(balance.unrealized, true),
                })
              : undefined
          }
        />
        <StatTile
          cell
          icon={<Activity className="h-4 w-4" />}
          label={t("live.overview.net")}
          value={<span className={toneClass(totals.net)}>{amount(totals.net, true)}</span>}
          hint={t("live.overview.net_hint", {
            // bare numbers: the unit is in the value above, and the hint must fit one line
            realized: coin(locale, totals.realized, { signed: true, unit: "" }),
            fees: coin(locale, totals.fees, { unit: "" }),
            funding: coin(locale, totals.funding, { signed: true, unit: "" }),
          })}
        />
        <StatTile
          cell
          icon={<CalendarRange className="h-4 w-4" />}
          label={t("live.overview.net_30d")}
          value={<span className={toneClass(recent.net)}>{amount(recent.net, true)}</span>}
          hint={t("live.overview.net_30d_hint", { n: recent.closed })}
        />
        <StatTile
          cell
          icon={<Scale className="h-4 w-4" />}
          tone={errors > 0 ? "danger" : warnings > 0 ? "warn" : "ok"}
          label={t("live.overview.findings")}
          value={String(errors + warnings + infos)}
          hint={t("live.overview.findings_hint", { errors, warnings, infos })}
        />
      </StatGroup>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title={t("live.overview.pnl_chart")}>
          <Panel className="p-4">
            <LiveLineChart
              testId="live-pnl-chart"
              label={t("live.overview.pnl_chart")}
              points={data.pnl_series.map((p) => ({
                ts: Date.parse(`${p.day}T00:00:00Z`),
                value: p.cumulative,
              }))}
              format={(v) => amount(v, true)}
              tickFormat={day}
              empty={t("live.overview.no_series")}
            />
          </Panel>
        </Section>
        <Section title={t("live.overview.balance_chart")}>
          <Panel className="p-4">
            <LiveLineChart
              testId="live-balance-chart"
              label={t("live.overview.balance_chart")}
              points={data.balance_series.map((b) => ({ ts: b.ts_ms, value: b.wallet }))}
              format={(v) => amount(v)}
              tickFormat={(ts) => dateTime(locale, ts)}
              color="hsl(var(--info))"
              step
              empty={t("live.overview.no_series")}
            />
          </Panel>
        </Section>
      </div>

      <Section title={t("live.overview.facts")}>
        <Panel className="p-5">
          <FactRows
            rows={[
              { label: t("live.overview.closed"), value: String(totals.closed) },
              {
                label: t("live.overview.open"),
                value: t("live.overview.open_value", {
                  n: data.open_positions,
                  unrealized: amount(data.open_unrealized, true),
                }),
              },
              { label: t("live.overview.fills"), value: String(data.fills) },
              { label: t("live.overview.orders"), value: String(data.orders) },
              {
                label: t("live.overview.win_rate"),
                value:
                  totals.win_rate === null
                    ? "—"
                    : t("live.overview.win_rate_value", {
                        rate: pct(locale, totals.win_rate, { digits: 0 }),
                        wins: totals.wins,
                        losses: totals.losses,
                      }),
              },
              {
                label: t("live.overview.last_sync"),
                value: source.last_sync_ms
                  ? `${dateTime(locale, source.last_sync_ms)} (${relative(locale, source.last_sync_ms, now)})`
                  : t("live.overview.never"),
              },
              {
                label: t("live.overview.last_run"),
                value: data.last_run
                  ? t("live.overview.run_value", {
                      kind: t(`live.sources.kind.${data.last_run.kind}`),
                      status: t(`live.sources.status.${data.last_run.status}`),
                      time: dateTime(locale, data.last_run.finished_ms ?? data.last_run.started_ms),
                    })
                  : t("live.overview.never"),
              },
            ]}
          />
        </Panel>
      </Section>
    </div>
  );
}
