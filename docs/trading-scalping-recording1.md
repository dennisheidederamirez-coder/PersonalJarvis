# Order-book recording 1 (2026-10-10, owner-approved, one session)

| | |
|---|---|
| Window | 2026-10-10 07:57:55 → 08:58:00 UTC (60.1 min wall; data spans 59.97 min) |
| Stop | `duration reached`; exit 0; no reconnects, no errors |
| Source | Binance USD-M public market streams, no key; the legacy combined-stream URL |
| Data | 14,280 top-20 book snapshots (7,140 per symbol), 14.7 MB |
| **Trades / liquidations** | **0 / 0. Not recorded: see "Defect".** |
| Paper test | Untouched; its 08:05 run completed normally during the session |

## Defect: no trades and no liquidations

Since Binance split its USD-M WebSocket endpoints (notice of 2026-03; legacy
URLs retired 2026-04-23), the legacy URL delivers **only** the `/public`
category (book data). `aggTrade` and `forceOrder` moved to `/market`. The
pre-flight check missed this. The code is fixed: one connection per category,
offline-tested. A new session needs a new owner approval.

## Data quality

| | BTCUSDT | ETHUSDT |
|---|---|---|
| Snapshots / expected (2 per s) | 7,140 / 7,197 (99.2 %) | 7,140 / 7,198 (99.2 %) |
| Gaps over 1 s | 0 (max 528 ms) | 0 (max 600 ms) |
| Price move in the hour | 0.11 % range (very quiet Saturday morning) | 0.16 % range |

The latency median (receive minus event time) is −3 ms. The local clock and
the venue clock are a few ms apart, so latency cannot be measured from these
timestamps.

## Spread and depth (observed)

| | BTCUSDT | ETHUSDT |
|---|---|---|
| Spread median / p90 / max | 0.012 / 0.012 / 0.12 bp (1 tick, max 10 ticks) | 0.040 / 0.040 / 0.040 bp (always 1 tick) |
| Size at the best bid / ask (median) | 8.3 / 4.8 BTC | 155 / 140 ETH |
| Visible top-20 notional, bid / ask (median) | ≈ 1.0 M / 0.57 M USD | ≈ 0.79 M / 0.63 M USD |

**Limit:** the 20 levels span only ≈ 0.25 bp (BTC) and ≈ 0.8 bp (ETH) from the
mid, so the depth "within 5–25 bp" equals the top-20 total. Deeper liquidity
is not visible in this stream.

## Liquidity over the hour (observed, 5-minute medians, top-20 notional)

- **BTC:** bid 0.48–2.38 M USD, ask 0.34–0.91 M USD, with a bid-heavy book most of the hour. The bid peaked at 08:50.
- **ETH:** bid 0.53–1.42 M USD, ask 0.41–0.87 M USD.
- **Spread:** constant at 1 tick throughout, for both.

## Execution costs

| Basis | What | Estimate |
|---|---|---|
| Observed book, modelled consumption | Market order 10 k USD | +0.006 bp (BTC) / +0.02 bp (ETH) against the mid: half a tick |
| Observed book, modelled consumption | Market order 100 k USD | Same in 99 % of snapshots; about 1 % exceed the top 20 |
| Observed book, modelled consumption | Market order 1 M USD | Exceeds the visible top 20 in 64 % (BTC) / 70 % (ETH) of cases, so not measurable here |
| Fees (base tier) | Taker / maker per fill | 5 bp / 2 bp |
| **Taker round trip, 10 k USD** | | **≈ 10.0 bp + latency drift (phase-1 samples: median 0.0–0.4 bp)** |
| **Maker round trip** | | **≈ 4 bp + adverse selection ≈ 0.3–0.5 bp per fill** |

## Limit-order fills: bounds from the book alone (no fill observed)

The test places a hypothetical post-only order at the best bid or ask every
10 s:

- **Lower bound:** a fill is CERTAIN if the opposite side later reaches our price.
- **Upper bound:** a fill is POSSIBLE if our level later empties.

With a constant 1-tick spread the two coincide.

| | BTCUSDT | ETHUSDT |
|---|---|---|
| Filled within 60 s | 41 % | 52 % |
| Filled within 300 s | 77 % | 82 % |
| Mid move 60 s after a certain fill (adverse selection) | −0.49 bp | −0.49 bp (60 s TTL), −0.32 bp (300 s) |
| Mid move 60 s after any placement | 0.0 bp | 0.0 bp |

**Limits of these fill numbers:**

- The order is assumed to be resting (latency ignored) and never cancelled.
- 500 ms snapshots can miss brief crossings, so the true rate may be higher.
- Without trades, the queue model (`maker_fill_experiment`) could not run.
- One quiet weekend hour does not represent busy or stressed markets.

Hidden and iceberg orders, and cancellations ahead in the queue, stay unknown.

## Consequences for the scalping work

1. **Costs are fees.** The spread is one tick, and impact is negligible up to about 100 k USD. The taker round trip is ≈ 0.10 % and the maker round trip ≈ 0.04–0.05 %. The 0.15 % research assumption and the `base` and `harsh` scenarios are conservative for normal hours.
2. **Adverse selection is measurable and small:** about 0.3–0.5 bp per maker fill, which matches the `base` scenario (0.5 bp). `harsh` (2 bp) is a stress case.
3. **The bar-level maker rule may be too strict.** It requires price to trade 1.5 bp through the limit, which is about 120 BTC ticks, whereas the book shows a best-price order is often filled by a one-tick move. Calibrating `through_bps` downwards would raise simulated maker fills.
4. **This does not create an edge.** Phase 2 showed the candidate signals have no gross edge even at zero cost. Better execution realism cannot fix that.
5. **Before any further conclusion:** one session with trades (fixed code), ideally in a busier weekday hour, to run the queue model and measure fills and drift with trades. That needs a new owner approval.

Files: `~/pj-trading-data/scalp/recording_1.sqlite`, `recording_1_report.json`,
`recording_1.log`.
