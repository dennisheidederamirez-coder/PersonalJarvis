# Scalping research branch

The agent should handle every holding period. The holding period is not a
goal: a strategy is judged only on its measured net performance and risk,
however quickly it closes a trade. Scalping is its own research branch. It
is developed and validated separately from the trend, breakout and
mean-reversion families, with its own multiple-testing budget.

Nothing here is active. Large downloads and standing data connections need
the owner's approval first.

## Holding-period classes and the data each needs

| Class | Typical hold | Minimum honest data | Bar backtests? |
|---|---|---|---|
| Ultra-scalping | seconds to a few minutes | tick / individual trades **and** order book (L2) with timestamps | **no** |
| Scalping | 1–15 minutes | 1m bars **plus** trades (aggressor side) for spread, delta and fill checks | only as a coarse pre-filter |
| Intraday | 15 minutes to hours | 1m–15m bars, trades optional | yes |
| Day trading | within one day | 5m–1h bars | yes |
| Swing | hours to days | 1h–1d bars | yes (phases 1–3) |

Why bars are not enough for short holds:

- A bar hides the order of high and low, the spread and the queue.
- With taker fees of about 0.05 % per side plus slippage, a round trip costs
  about 0.15 %. Phase 2 already showed that 1h strategies lose after costs.
  Short holds need an edge larger than that, usually maker execution, and
  a simulator that knows the spread and the queue.

## What exists and is reused

- **Timeframes:** 1m, 3m, 5m, 15m, 1h, 4h and 1d in `timeframes.py`.
  Resampling builds complete bars only, and a higher bar is visible only
  after it closes. The venue adapters serve 1m/3m/5m (Coinbase has no 3m).
- **Order-flow features** (`orderflow.py`): bar delta and CVD (from
  venues reporting taker volume), book imbalance, liquidity walls and their
  changes. Absorption and spoofing appear only as low-confidence hints.
- **Liquidations** (`liquidations.py`): observed and modelled zones,
  optional.
- **Unchanged guards:**
  - the risk manager, leverage tiers, portfolio and correlation limits,
    daily halt and kill switch;
  - the strategy book (only validated strategies trade, objective ranking);
  - the guard that forbids model imports, so no AI order without validated
    rules.

## What has to be built (in this order)

1. **Trade-level simulator** (`microsim.py`, new). It is event-driven over
   trades and book updates. It models:
   - order types: market, limit and post-only;
   - spread from the best bid and offer;
   - a queue-position model for limit orders;
   - partial fills from traded volume at the price level;
   - configurable latency (decision to order to fill);
   - maker and taker fees, funding;
   - a "stale data" guard.

   It reuses the risk manager, the paper broker's accounting and the
   journal. The bar engine stays as it is.
2. **Trade data.** Free historical trade data exists in two forms:
   - The paged REST endpoint (Binance `aggTrades`) is too slow for long
     histories.
   - Bulk trade files allow personal, non-production backtesting only, not
     live use.

   Using them for research backtests is an owner decision. Live trading
   decisions would always use the venue's live feed.
3. **Order-book data.** No venue publishes book history. Only a recorder of
   the live book (snapshot plus deltas) builds one. It needs a standing
   WebSocket connection (owner approval), storage limits and retention.
4. **Scalping strategy families**, separate from the existing ones, for
   example:
   - order-book imbalance continuation;
   - delta divergence at session extremes;
   - a 1m–5m opening-range breakout.

   Each gets its own walk-forward validation on trade-level simulation and
   its own test count.
5. **Scalping-specific limits**, on top of the unchanged global ones:
   - a maximum number of trades per day;
   - a cooldown after consecutive losses;
   - a maximum data age;
   - a maximum observed latency.

## Rules that do not change

- No forced trades. A strategy that shows no supported edge does not trade,
  whatever its timeframe.
- No AI-only orders: every entry comes from a pre-defined, validated rule
  and passes the independent risk manager.
- Leverage limits, the per-trade risk cap and the kill switch are the same
  for every holding period.
- Validated scalping strategies join the same paper-trading framework
  (`docs/trading-paper-test-spec.md`) through the strategy book, attributed
  and evaluated separately.
