/**
 * Trading — a read-only dashboard with two strictly separate views.
 *
 * PAPER (the default): seven tabs over `/api/trading` (a read model over the
 * paper job's SQLite journal): overview with the 14-day test, strategies,
 * positions & risk, signals & decisions, performance, data & operations,
 * daily reports.
 *
 * LIVE: the real account's history from the LIVE archive, in its own
 * component (`components/trading/live/LiveDesk.tsx`) over its own routes
 * (`/api/trading/live/*`) — it never shows a paper number and the paper tabs
 * never show a LIVE one. Switching mode switches which queries run.
 *
 * There is no button here that starts, stops, trades, syncs, imports or
 * configures anything — the routes behind both views are GET-only.
 *
 * The section speaks German by default and switches to English in its header
 * (`components/trading/tradingI18n.ts`).
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, RefreshCw } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  IconButton,
  PanelHeader,
  SegmentedFilter,
} from "@/components/extensions/primitives";
import { DecisionsTab } from "@/components/trading/DecisionsTab";
import { LiveDesk } from "@/components/trading/live/LiveDesk";
import { HealthTab } from "@/components/trading/HealthTab";
import { OverviewTab } from "@/components/trading/OverviewTab";
import { PerformanceTab } from "@/components/trading/PerformanceTab";
import { PositionsTab } from "@/components/trading/PositionsTab";
import { ReportsTab } from "@/components/trading/ReportsTab";
import { StrategiesTab } from "@/components/trading/StrategiesTab";
import { SourceNotice, WarningList } from "@/components/trading/parts";
import { dateTime } from "@/components/trading/tradingFormat";
import {
  TRADING_LANGUAGES,
  type TradingLanguage,
  useTradingI18n,
} from "@/components/trading/tradingI18n";
import {
  useLiveOverview,
  useTradingHealth,
  useTradingOverview,
  useTradingPerformance,
  useTradingPositions,
  useTradingReports,
  useTradingStrategies,
} from "@/hooks/useTrading";
import type { TradingSource } from "@/types/trading";

const TABS = [
  "overview",
  "strategies",
  "positions",
  "decisions",
  "performance",
  "health",
  "reports",
] as const;
type Tab = (typeof TABS)[number];
type Mode = "paper" | "live";
const MODES: readonly Mode[] = ["paper", "live"];

export function TradingDeskView() {
  const { lang, setLang, t, locale } = useTradingI18n();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [mode, setMode] = useState<Mode>("paper");
  const paper = mode === "paper";

  // Only the active mode's queries run: a LIVE view polls nothing of the paper journal.
  const overview = useTradingOverview(paper);
  const performance = useTradingPerformance(
    paper && (tab === "overview" || tab === "performance"),
  );
  const strategies = useTradingStrategies(paper && tab === "strategies");
  const positions = useTradingPositions(paper && tab === "positions");
  const health = useTradingHealth(paper && tab === "health");
  const reports = useTradingReports(paper && tab === "reports");
  const live = useLiveOverview(!paper);
  const liveSource = live.data?.source;

  const source: TradingSource | undefined = overview.data?.source;
  // "3 hours ago" is measured from the moment the server read the journal, so
  // relative times always agree with the numbers they sit next to.
  const now = source?.read_at_ms ?? 0;
  const sourceOk = source?.state === "ok";
  const fetching =
    overview.isFetching ||
    performance.isFetching ||
    strategies.isFetching ||
    positions.isFetching ||
    health.isFetching ||
    reports.isFetching ||
    live.isFetching;
  const failed = overview.error instanceof Error ? overview.error.message : null;
  const capital = overview.data?.accounts[0]?.capital ?? 0;

  /**
   * A tab's content, or the notice that explains why there is none. While the
   * overview already shows a missing or unreadable journal above the tabs, a
   * tab does not repeat it.
   */
  function body<T extends { source: TradingSource }>(
    data: T | undefined,
    render: (d: T) => JSX.Element,
  ) {
    if (!data) return <Loading text={t("loading")} />;
    if (data.source.state !== "ok") {
      return sourceOk ? <SourceNotice source={data.source} t={t} /> : null;
    }
    return render(data);
  }

  return (
    <ScrollArea className="h-full">
      <div className="flex w-full flex-col gap-4 px-8 py-6" data-testid="trading-desk">
        <PanelHeader
          title={paper ? t("title") : t("live.title")}
          subtitle={
            paper
              ? source?.journal_mtime_ms
                ? `${t("subtitle")} ${t("updated", { time: dateTime(locale, source.journal_mtime_ms) })}`
                : t("subtitle")
              : liveSource?.archive_mtime_ms
                ? `${t("live.subtitle")} ${t("live.updated", { time: dateTime(locale, liveSource.archive_mtime_ms) })}`
                : t("live.subtitle")
          }
          actions={
            <>
              <SegmentedFilter<Mode>
                label={t("mode.label")}
                value={mode}
                onChange={setMode}
                options={MODES.map((m) => ({ id: m, label: t(`mode.${m}`) }))}
              />
              <span className="mx-2 h-6 w-px bg-border" aria-hidden />
              <SegmentedFilter<TradingLanguage>
                label={t("language")}
                value={lang}
                onChange={setLang}
                options={TRADING_LANGUAGES.map((l) => ({ id: l, label: l.toUpperCase() }))}
              />
              <IconButton
                label={t("refresh")}
                busy={fetching}
                onClick={() =>
                  void queryClient.refetchQueries({ queryKey: ["trading"], type: "active" })
                }
              >
                <RefreshCw className="h-4 w-4" />
              </IconButton>
            </>
          }
        />

        {paper ? (
          <>
            <div
              role="note"
              data-testid="trading-paper-banner"
              className="flex items-start gap-3 rounded-lg border border-info/40 bg-info/10 px-4 py-3 text-sm text-foreground"
            >
              <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden />
              <span>{t("banner")}</span>
            </div>

            {failed ? (
              <div
                role="alert"
                className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm"
              >
                {t("request_failed", { error: failed })}
              </div>
            ) : null}
            {source && !sourceOk ? <SourceNotice source={source} t={t} /> : null}
            {source ? <WarningList source={source} t={t} /> : null}

            <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
              <TabsList className="flex-wrap justify-start">
                {TABS.map((id) => (
                  <TabsTrigger key={id} value={id} data-testid={`trading-tab-${id}`}>
                    {t(`tabs.${id}`)}
                  </TabsTrigger>
                ))}
              </TabsList>

              <TabsContent value="overview" className="mt-5">
                {body(overview.data, (d) => (
                  <OverviewTab
                    data={d}
                    performance={performance.data}
                    locale={locale}
                    t={t}
                    now={now}
                  />
                ))}
              </TabsContent>
              <TabsContent value="strategies" className="mt-5">
                {body(strategies.data, (d) => (
                  <StrategiesTab data={d} locale={locale} lang={lang} t={t} now={now} />
                ))}
              </TabsContent>
              <TabsContent value="positions" className="mt-5">
                {body(positions.data, (d) => (
                  <PositionsTab data={d} locale={locale} lang={lang} t={t} />
                ))}
              </TabsContent>
              <TabsContent value="decisions" className="mt-5">
                {tab === "decisions" ? <DecisionsTab locale={locale} lang={lang} t={t} /> : null}
              </TabsContent>
              <TabsContent value="performance" className="mt-5">
                {body(performance.data, (d) => (
                  <PerformanceTab
                    data={d}
                    locale={locale}
                    lang={lang}
                    t={t}
                    capital={capital}
                  />
                ))}
              </TabsContent>
              <TabsContent value="health" className="mt-5">
                {body(health.data, (d) => (
                  <HealthTab
                    data={d}
                    source={d.source}
                    locale={locale}
                    lang={lang}
                    t={t}
                    now={now}
                  />
                ))}
              </TabsContent>
              <TabsContent value="reports" className="mt-5">
                {body(reports.data, (d) => (
                  <ReportsTab data={d} locale={locale} t={t} />
                ))}
              </TabsContent>
            </Tabs>
          </>
        ) : (
          <LiveDesk lang={lang} locale={locale} t={t} />
        )}
      </div>
    </ScrollArea>
  );
}

function Loading({ text }: { text: string }) {
  return (
    <div role="status" aria-busy="true" className="py-10 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}
