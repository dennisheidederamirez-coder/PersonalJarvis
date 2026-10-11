/**
 * LIVE history — the real account, read from the LIVE archive.
 *
 * Shown by `TradingDeskView` when its header switch is on LIVE. It shares the
 * section's frame (header, language, reload) with the paper dashboard and
 * nothing else: its own GET routes under `/api/trading/live`, its own types,
 * its own banner, notices and warnings, so a paper number can never appear
 * here and a LIVE number never in the paper tabs. There is no button that
 * syncs, imports or trades; new data arrives only through a sync or CSV import
 * the owner starts on the command line.
 */
import { useState } from "react";
import { FlaskConical, Landmark } from "lucide-react";
import { cn } from "@/lib/utils";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useLiveCosts,
  useLiveOverview,
  useLiveSources,
} from "@/hooks/useTrading";
import type { TradingLiveSource } from "@/types/trading";
import type { TradingLanguage, TradingT } from "../tradingI18n";
import { LiveCostsTab } from "./LiveCostsTab";
import { LiveOverviewTab } from "./LiveOverviewTab";
import { LivePositionsTab } from "./LivePositionsTab";
import { LiveReconciliationTab } from "./LiveReconciliationTab";
import { LiveSourcesTab } from "./LiveSourcesTab";
import { LiveTradesTab } from "./LiveTradesTab";
import { LiveSourceNotice, LiveWarningList } from "./liveParts";

const TABS = ["overview", "positions", "trades", "costs", "reconciliation", "sources"] as const;
type Tab = (typeof TABS)[number];

export function LiveDesk({
  lang,
  locale,
  t,
}: {
  lang: TradingLanguage;
  locale: string;
  t: TradingT;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const overview = useLiveOverview();
  const costs = useLiveCosts(tab === "costs");
  const sources = useLiveSources(tab === "sources");
  const source: TradingLiveSource | undefined = overview.data?.source;
  const sourceOk = source?.state === "ok";
  const origin = source?.state === "ok" ? source.origin : "unknown";
  const failed = overview.error instanceof Error ? overview.error.message : null;

  function body<T extends { source: TradingLiveSource }>(
    data: T | undefined,
    render: (d: T) => JSX.Element,
  ) {
    if (!data) return <Loading text={t("live.loading")} />;
    if (data.source.state !== "ok") {
      return sourceOk ? <LiveSourceNotice source={data.source} t={t} /> : null;
    }
    return render(data);
  }

  return (
    <div className="flex flex-col gap-4" data-testid="trading-live">
      {/* Real account data and generated demo data never look alike: the
          banner says which one this archive holds (lh_meta.origin). Until it
          is known, it claims neither. */}
      <div
        role="note"
        data-testid="trading-live-banner"
        data-origin={origin}
        className={cn(
          "flex items-start gap-3 rounded-lg px-4 py-3 text-sm text-foreground",
          origin === "synthetic"
            ? "border-2 border-dashed border-info/60 bg-info/10"
            : "border border-warning/50 bg-warning/10",
        )}
      >
        {origin === "synthetic" ? (
          <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden />
        ) : (
          <Landmark className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
        )}
        <div className="flex flex-col gap-1">
          <span className="font-medium">
            {origin === "synthetic"
              ? t("live.banner_synthetic")
              : origin === "real"
                ? t("live.banner")
                : t("live.banner_pending")}
          </span>
          <span className="text-muted-foreground">{t("live.definitions")}</span>
        </div>
      </div>

      {failed ? (
        <div
          role="alert"
          className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm"
        >
          {t("request_failed", { error: failed })}
        </div>
      ) : null}
      {source && !sourceOk ? <LiveSourceNotice source={source} t={t} /> : null}
      {source ? <LiveWarningList source={source} t={t} /> : null}

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <TabsList className="h-auto flex-wrap justify-start">
          {TABS.map((id) => (
            <TabsTrigger key={id} value={id} data-testid={`live-tab-${id}`}>
              {t(`live.tabs.${id}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview" className="mt-5">
          {body(overview.data, (d) => (
            <LiveOverviewTab data={d} source={d.source} locale={locale} t={t} />
          ))}
        </TabsContent>
        <TabsContent value="positions" className="mt-5">
          {tab === "positions" && sourceOk ? <LivePositionsTab locale={locale} t={t} /> : null}
        </TabsContent>
        <TabsContent value="trades" className="mt-5">
          {tab === "trades" && sourceOk ? <LiveTradesTab locale={locale} t={t} /> : null}
        </TabsContent>
        <TabsContent value="costs" className="mt-5">
          {body(costs.data, (d) => (
            <LiveCostsTab data={d} locale={locale} t={t} />
          ))}
        </TabsContent>
        <TabsContent value="reconciliation" className="mt-5">
          {tab === "reconciliation" && sourceOk ? (
            <LiveReconciliationTab locale={locale} lang={lang} t={t} />
          ) : null}
        </TabsContent>
        <TabsContent value="sources" className="mt-5">
          {body(sources.data, (d) => (
            <LiveSourcesTab data={d} locale={locale} lang={lang} t={t} />
          ))}
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Loading({ text }: { text: string }) {
  return (
    <div role="status" aria-busy="true" className="py-10 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}
