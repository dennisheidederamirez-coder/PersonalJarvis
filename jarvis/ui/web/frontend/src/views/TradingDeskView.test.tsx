import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  stubFetch(RESPONSES);
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
    expect(screen.getByText("Läuft")).toBeTruthy();
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
    await screen.findByText("Noch keine Tagesberichte.", { exact: false });
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
    expect(notice.textContent).toContain("Kein Paper-Journal gefunden");
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
    expect(within(list).getByText(/überfällig/)).toBeTruthy();
  });

  it("translates reasons and keeps the journal's own text", async () => {
    renderView(<TradingDeskView />);
    await screen.findByText("14-Tage-Paper-Test");
    fireEvent.mouseDown(screen.getByTestId("trading-tab-decisions"));
    expect((await screen.findAllByText("Limit für offenes Risiko")).length).toBe(2); // chip + row
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
