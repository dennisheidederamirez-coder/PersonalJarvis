import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LIVE_RESPONSES,
  liveMissingSource,
  liveOverview,
  liveReconciliation,
} from "@/components/trading/live/liveFixtures";
import { RESPONSES, missingSource, overview } from "@/components/trading/testFixtures";
import {
  DEFAULT_TRADING_LANGUAGE,
  flatKeys,
  translate,
  useTradingLanguageStore,
} from "@/components/trading/tradingI18n";
import { TradingDeskView } from "@/views/TradingDeskView";

function renderView(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const calls: { url: string; method: string }[] = [];

function stubFetch(responses: Record<string, unknown>) {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: (init?.method ?? "GET").toUpperCase() });
      const name = url.replace("/api/trading/", "").split("?")[0];
      if (!(name in responses)) return new Response("not found", { status: 404 });
      return new Response(JSON.stringify(responses[name]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
  useTradingLanguageStore.setState({ lang: DEFAULT_TRADING_LANGUAGE });
  stubFetch({ ...RESPONSES, ...LIVE_RESPONSES });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("trading dashboard i18n", () => {
  it("opens in German", () => {
    expect(DEFAULT_TRADING_LANGUAGE).toBe("de");
  });

  it("has the same keys in German and English", () => {
    expect(flatKeys("de")).toEqual(flatKeys("en"));
  });

  it("fills placeholders", () => {
    expect(translate("de", "test.day", { day: 3, total: 14 })).toBe("Tag 3 von 14");
    expect(translate("en", "test.day", { day: 3, total: 14 })).toBe("Day 3 of 14");
  });
});

describe("TradingDeskView", () => {
  it("shows the overview in German with the paper banner", async () => {
    renderView(<TradingDeskView />);
    expect(await screen.findByText("14-Tage-Paper-Test")).toBeTruthy();
    expect(screen.getByTestId("trading-paper-banner").textContent).toContain("Simulation");
    expect(screen.getByText("Tag 1 von 14")).toBeTruthy();
    expect(screen.getByText("Läuft")).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(screen.getAllByText("Konto A").length).toBeGreaterThan(0);
  });

  it("switches to English and remembers it", async () => {
    renderView(<TradingDeskView />);
    await screen.findByText("14-Tage-Paper-Test");
    fireEvent.click(screen.getByRole("tab", { name: "EN" }));
    expect(await screen.findByText("14-day paper test")).toBeTruthy();
    expect(localStorage.getItem("jarvis.trading.language")).toBe("en");
  });

  it("only ever reads: every request is a GET under /api/trading", async () => {
    renderView(<TradingDeskView />);
    await screen.findByText("14-Tage-Paper-Test");
    for (const tab of ["strategies", "positions", "decisions", "performance", "health", "reports"]) {
      fireEvent.mouseDown(screen.getByTestId(`trading-tab-${tab}`));
      fireEvent.click(screen.getByTestId(`trading-tab-${tab}`));
    }
    await screen.findByText("Noch keine Tagesberichte.", { exact: false }); // i18n-allow: asserts the German default UI text
    expect(calls.length).toBeGreaterThan(0);
    for (const c of calls) {
      expect(c.method).toBe("GET");
      expect(c.url.startsWith("/api/trading/")).toBe(true);
    }
  });

  it("offers no action besides tabs, language and reload", async () => {
    renderView(<TradingDeskView />);
    await screen.findByText("14-Tage-Paper-Test");
    const labels = screen
      .getAllByRole("button")
      .map((b) => (b.getAttribute("aria-label") ?? b.textContent ?? "").trim());
    expect(labels).toEqual(["Neu laden"]);
  });

  it("explains a missing journal instead of failing", async () => {
    stubFetch({ ...RESPONSES, overview: { ...overview, source: missingSource, accounts: [] } });
    renderView(<TradingDeskView />);
    const notice = await screen.findByTestId("trading-source-notice");
    expect(notice.textContent).toContain("Kein Paper-Journal gefunden"); // i18n-allow: asserts the German default UI text
    expect(notice.textContent).toContain("paper_journal.sqlite");
    expect(screen.queryByText("Konto A")).toBeNull();
  });

  it("shows warnings from the journal", async () => {
    stubFetch({
      ...RESPONSES,
      overview: { ...overview, source: { ...overview.source, warnings: ["run_overdue"] } },
    });
    renderView(<TradingDeskView />);
    const list = await screen.findByTestId("trading-warnings");
    expect(within(list).getByText(/überfällig/)).toBeTruthy(); // i18n-allow: asserts the German default UI text
  });

  it("translates reasons and keeps the journal's own text", async () => {
    renderView(<TradingDeskView />);
    await screen.findByText("14-Tage-Paper-Test");
    fireEvent.mouseDown(screen.getByTestId("trading-tab-decisions"));
    expect((await screen.findAllByText("Limit für offenes Risiko")).length).toBe(2); // chip + row; i18n-allow: asserts the German default UI text
    expect(screen.getByText("total open risk would exceed the limit")).toBeTruthy();
    expect(screen.getByText("Signal: Ausbruch nach oben")).toBeTruthy();
  });

  it("shows positions with their risk to the stop", async () => {
    renderView(<TradingDeskView />);
    await screen.findByText("14-Tage-Paper-Test");
    fireEvent.mouseDown(screen.getByTestId("trading-tab-positions"));
    const tab = await screen.findByTestId("trading-positions");
    expect(within(tab).getByText("Long")).toBeTruthy();
    expect(within(tab).getAllByRole("meter").length).toBe(5);
  });
});

describe("TradingDeskView — LIVE history", () => {
  async function openLive() {
    renderView(<TradingDeskView />);
    await screen.findByText("14-Tage-Paper-Test");
    const before = calls.length;
    fireEvent.click(screen.getByRole("tab", { name: "LIVE" }));
    await screen.findByTestId("trading-live-banner");
    return before;
  }

  it("opens on PAPER and switches to a separately labelled LIVE view", async () => {
    await openLive();
    expect(screen.queryByTestId("trading-paper-banner")).toBeNull();
    expect(await screen.findByTestId("live-overview")).toBeTruthy();
    expect(screen.queryByText("14-Tage-Paper-Test")).toBeNull();
  });

  it("labels a synthetic archive as demo data, never as the real account", async () => {
    await openLive();
    await screen.findByTestId("live-overview");
    const banner = screen.getByTestId("trading-live-banner");
    expect(banner.getAttribute("data-origin")).toBe("synthetic");
    expect(banner.textContent).toContain("synthetische Testdaten"); // i18n-allow: asserts the German default UI text
    expect(banner.textContent).not.toContain("echte Kontodaten"); // i18n-allow: asserts the German default UI text
    expect(screen.getByText("Trading (LIVE-Historie · Demodaten)")).toBeTruthy();
    expect(screen.getByText(/synthetischer Demodaten/)).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(screen.queryByText(/des echten Kontos/)).toBeNull();
  });

  it("labels a real archive as real account data", async () => {
    stubFetch({
      ...RESPONSES,
      ...LIVE_RESPONSES,
      "live/overview": { ...liveOverview, source: { ...liveOverview.source, origin: "real" } },
    });
    await openLive();
    await screen.findByTestId("live-overview");
    const banner = screen.getByTestId("trading-live-banner");
    expect(banner.getAttribute("data-origin")).toBe("real");
    expect(banner.textContent).toContain("echte Kontodaten"); // i18n-allow: asserts the German default UI text
    expect(screen.getByText("Trading (LIVE-Historie)")).toBeTruthy();
  });

  it("ties every difference to its records, sources and cause", async () => {
    await openLive();
    fireEvent.mouseDown(screen.getByTestId("live-tab-reconciliation"));
    const tab = await screen.findByTestId("live-reconciliation");
    const rows = within(tab).getAllByTestId("live-finding");
    expect(rows.length).toBe(7);
    // internal: the position and the fills that were summed, compared with their sum
    expect(within(tab).getByText(/Fills: T\d+/)).toBeTruthy();
    expect(within(tab).getByText("Summe der Fills")).toBeTruthy();
    // journal: position ↔ entry, the journal named as the other source
    expect(within(tab).getAllByText(/^Position P\d+$/).length).toBeGreaterThan(3);
    expect(within(tab).getAllByText(/^Journal-Eintrag J-\d+$/).length).toBeGreaterThan(2); // i18n-allow: asserts the German default UI text
    expect(within(tab).getAllByText("Journal control").length).toBeGreaterThan(2);
    // the P/L cause names the definition both sides were aligned to
    expect(within(tab).getByText(/Definition des Journals umgerechnet: netto/)).toBeTruthy(); // i18n-allow: asserts the German default UI text
    // balance: the two observations
    expect(within(tab).getByText(/^zwischen .+ und .+$/)).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).getByTestId("live-scroll-table")).toBeTruthy();
  });

  it("labels derived ids and keeps transaction totals off single positions", async () => {
    const base = liveReconciliation.findings[0];
    const exportFinding = {
      ...base,
      id: "export:export_pnl_inconsistent:x:3f2a91c07b5e4d18",
      code: "export_pnl_inconsistent",
      severity: "warning",
      group: "export",
      field: "net",
      position_id: "x:3f2a91c07b5e4d18",
      entry_id: null,
      venue_source: "csv",
      other_source: "export_components",
      fill_ids: [],
      venue_value: -45.66,
      other_value: -1.18,
      difference: -44.48,
    };
    const totalsFinding = {
      ...base,
      id: "totals:fill_totals_mismatch:SOLUSDT:fee",
      code: "fill_totals_mismatch",
      severity: "error",
      group: "totals",
      field: "fee",
      position_id: null,
      entry_id: null,
      symbol: "SOLUSDT",
      side: null,
      opened_ms: null,
      closed_ms: null,
      venue_source: null,
      other_source: "fill_totals",
      fill_ids: [],
      period_from_ms: 1_782_777_600_000,
      period_to_ms: 1_782_864_000_000,
      venue_value: 0.3,
      other_value: 0.6,
      difference: -0.3,
    };
    stubFetch({
      ...RESPONSES,
      ...LIVE_RESPONSES,
      "live/reconciliation": {
        ...liveReconciliation,
        findings: [exportFinding, totalsFinding],
        counts: { export_pnl_inconsistent: 1, fill_totals_mismatch: 1 },
      },
    });
    await openLive();
    fireEvent.mouseDown(screen.getByTestId("live-tab-reconciliation"));
    const tab = await screen.findByTestId("live-reconciliation");
    expect(within(tab).getAllByTestId("live-finding").length).toBe(2);
    expect(within(tab).getByText("Position ohne Börsen-ID (abgeleitet: 3f2a91c0)")).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).getByText("Brutto − Gebühr + Funding (Export)")).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).getByText("keiner einzelnen Position zugeordnet")).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).getByText("Tagessummen Transaktions-Export")).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).queryByText(/^Position x:/)).toBeNull();
  });

  it("reads only LIVE routes once in LIVE mode, and only with GET", async () => {
    const before = await openLive();
    for (const tab of ["positions", "trades", "costs", "reconciliation", "sources"]) {
      fireEvent.mouseDown(screen.getByTestId(`live-tab-${tab}`));
      await screen.findByTestId(`live-${tab}`);
    }
    const after = calls.slice(before);
    expect(after.length).toBeGreaterThan(5);
    for (const c of after) {
      expect(c.method).toBe("GET");
      expect(c.url.startsWith("/api/trading/live/")).toBe(true);
    }
  });

  it("offers no action besides tabs, mode, language and reload", async () => {
    await openLive();
    await screen.findByTestId("live-overview");
    const labels = screen
      .getAllByRole("button")
      .map((b) => (b.getAttribute("aria-label") ?? b.textContent ?? "").trim());
    expect(labels).toEqual(["Neu laden"]);
  });

  it("shows the balance in USDT and the differences found", async () => {
    await openLive();
    const tab = await screen.findByTestId("live-overview");
    expect(within(tab).getAllByText(/USDT/).length).toBeGreaterThan(3);
    expect(within(tab).getByRole("group", { name: "Abweichungen" }).textContent).toContain("7");
  });

  it("translates reconciliation codes and states the journal is not independent", async () => {
    await openLive();
    fireEvent.mouseDown(screen.getByTestId("live-tab-reconciliation"));
    const tab = await screen.findByTestId("live-reconciliation");
    expect(within(tab).getByText("Gebühr weicht ab")).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).getByText(/Kontostandsänderung ohne Trade/)).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).getByText(/keine unabhängige/)).toBeTruthy(); // i18n-allow: asserts the German default UI text
  });

  it("lists import issues by line and field, never by content", async () => {
    await openLive();
    fireEvent.mouseDown(screen.getByTestId("live-tab-sources"));
    const tab = await screen.findByTestId("live-sources");
    expect(within(tab).getByText("Kein gültiger Zeitstempel")).toBeTruthy(); // i18n-allow: asserts the German default UI text
    expect(within(tab).getByText(/Zeile 4/)).toBeTruthy();
    expect(within(tab).queryByText(/not-a-time/)).toBeNull();
  });

  it("explains a missing archive instead of failing", async () => {
    stubFetch({
      ...RESPONSES,
      ...LIVE_RESPONSES,
      "live/overview": { ...liveOverview, source: liveMissingSource, balance: null },
    });
    await openLive();
    const notice = await screen.findByTestId("live-source-notice");
    expect(notice.textContent).toContain("Kein LIVE-Archiv gefunden"); // i18n-allow: asserts the German default UI text
    expect(notice.textContent).toContain("live_history.sqlite");
    expect(screen.queryByTestId("live-overview")).toBeNull();
  });

  it("shows LIVE warnings in LIVE wording", async () => {
    stubFetch({
      ...RESPONSES,
      ...LIVE_RESPONSES,
      "live/overview": {
        ...liveOverview,
        source: { ...liveOverview.source, warnings: ["sync_old"] },
      },
    });
    await openLive();
    const list = await screen.findByTestId("live-warnings");
    expect(within(list).getByText(/keine automatische Synchronisierung/)).toBeTruthy(); // i18n-allow: asserts the German default UI text
  });
});
