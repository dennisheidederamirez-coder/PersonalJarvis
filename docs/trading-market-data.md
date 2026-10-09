# Market data for the demo trading agent

Read-only, keyless market data for BTC and ETH, from public exchange APIs
(`jarvis/market_data/`). Everything here is a **research input**. No trade
depends on any single source. Strategies trade only on their own validated
rules and after the risk manager approves.

Facts below were checked against the venues' public documentation in
2026-10. Limits and terms change, so re-check before relying on a number.

## Sources

| Source | Bars | Funding | Open interest | Public limit | Notes |
|---|---|---|---|---|---|
| **Bybit V5** | spot + linear perps, 1000 per page, full history | history | history back to listing (5 min–1 d) | 600 requests / 5 s per IP | **Not for installs in the EEA:** global Bybit stopped serving EEA residents on 2026-07-01, and its terms neither allow nor exclude keyless access. Use OKX and Binance there. |
| **OKX V5** (primary perpetuals in the EEA) | spot + swaps, 100 per page, older than 3 months via `history-candles` | history | statistics | 20 requests / 2 s | Independent operator. |
| **Binance REST API** (perpetual backtests: price AND funding history from one venue) | spot + USD-M, 1000 per page; **taker-buy volume per bar** | history | last 30 days only | 6000 weight / min per IP | The REST API may be used by personal bots under Binance's terms. |
| **Binance Vision** bulk files | deep history | yes | yes | – | CC BY-NC-SA 4.0. Allowed only for personal non-production backtesting; **live trading or order generation is forbidden**. Not used by the agent. |
| **Coinbase Exchange** | spot only, USD quote, 300 per page | – | – | not published | Independent spot reference; also available to US users. |
| **The eventual demo venue** | its own feed | its own feed | – | per venue | Later: the trading venue's own mark price and funding for realistic paper fills. |

Rules built into the adapters:

- The still-open bar is dropped.
- Provenance is kept on every series (`<venue>:<spot|perp>:<symbol>`).
- Spot and perpetual data, and different venues, are never merged into one
  series. `crosscheck.choose` compares the primary with the backup and picks
  one whole series. If the two disagree, or neither is fresh and usable, the
  answer is "no data to trade on".

## Liquidations

| | Binance | Bybit | OKX |
|---|---|---|---|
| Observed liquidations (live) | WebSocket `forceOrder`: **only the largest per symbol per second** | WebSocket `allLiquidation`: **all** liquidations, 500 ms batches | WebSocket `liquidation-orders`: at most one per contract per second |
| History via public REST | no | no | no |

- Observed liquidations are only available live. A history exists only if
  Jarvis records it itself, which needs a standing WebSocket connection (an
  owner decision). Bybit's feed is the only complete one.
- Commercial heatmaps (e.g. CoinGlass) are paid for API use (from about
  $29/month), and their models are not disclosed. Their website is free to
  look at, but not for automated use.
- `jarvis/trading/liquidations.py` keeps two separate types:
  - `ObservedLiquidation`: what a venue reported, including how complete that
    venue's feed is.
  - `ModelledZone`: an estimate built from one venue's bars and open-interest
    increases under stated leverage assumptions. Each zone carries its
    assumptions.

  Modelled zones are removed once price has traded through them.
  `nearest_targets` lists the largest zones above and below the price as
  possible liquidity targets, and nothing more.

## Order flow

| | Binance | Bybit | OKX |
|---|---|---|---|
| Order book snapshot | spot depth up to 5000 levels | up to 1000 levels (full depth up to 10 000 per side) | up to 400 levels |
| Order book history | no | no | no |
| Aggressive trades | `aggTrades` history (paged) | recent trades only via REST | recent and history trades (limited) |
| Delta per bar from bars alone | **yes** (taker-buy volume in klines) | no | no |

- **CVD** works today, for free and with history, from Binance bars:
  `orderflow.bar_delta` / `cvd`. Other venues need recorded trades.
- **Book imbalance, liquidity walls and their changes** need snapshots over
  time. No venue offers book history, so it has to be recorded live, which
  again is an owner decision. The functions exist (`imbalance`, `walls`,
  `wall_changes`).
- **Absorption and spoofing** are reported only as `Hint`s with low
  confidence, evidence and caveats, never as facts. A vanished wall can
  equally be an honest cancellation.
- Spot and futures flow differ. Futures carry leverage, liquidations and
  funding-driven flow, and every venue's book is its own market. The
  functions refuse to combine sources.

## Priorities

**Now, free and reliable (no standing connection):**

1. Bars and funding from Binance USD-M (one venue for both), OKX as the
   cross-check, Coinbase as the spot reference. Bybit only where it may
   serve the user.
2. CVD and delta from Binance bars, with history.
3. Modelled liquidation zones from one venue's bars and open interest,
   labelled as a model.
4. Point-in-time book imbalance and walls, on request.

**Later, needs the owner's approval (standing connections, storage):**

5. Live recording of observed liquidations, from Bybit `allLiquidation`
   first, because it is complete.
6. Periodic book snapshots and trade recording, for wall changes and CVD on
   other venues. Data volume needs a retention limit.
7. A paid aggregated provider (e.g. CoinGlass), only if the free sources
   prove insufficient and the owner books it.

Every one of these feeds research and, if a rule built on it passes
walk-forward validation, a strategy. None is a precondition for trading.
