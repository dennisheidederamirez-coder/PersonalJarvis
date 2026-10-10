# Scalping research — inventory and development plan

Demo only. No real-money orders, no private exchange keys, no paid data, no
notifications, no new standing job. The running forward paper test (daily
breakout and 4h SMA trend) is untouched: it runs from a frozen copy and does
not import this branch. Background: [`trading-scalping.md`](trading-scalping.md).

## 1. Inventory

**Reused unchanged:**

- `trading.data`: `BarSeries` with provenance and aggressive buy volume, plus quality checks.
- `trading.timeframes`: M1/M3/M5, complete-bar resampling, and `align` (a higher bar only after it closed).
- `trading.indicators`: EMA, ATR, PVSRA, daily open.
- `trading.orderflow`: delta, CVD, imbalance.
- `trading.liquidations`: observed vs modelled zones.
- `trading.signal_strategy`: TradingView alerts as optional evidence.
- `trading.validation`: walk-forward, holdout, Bonferroni.
- `trading.metrics`, `trading.journal`, `trading.leverage` (tiers 5/10/20/30).
- `market_data`: Binance/OKX/Coinbase bar adapters, polite client, store and cross-check.

**Changed (neutral by default, so swing behaviour is unchanged):**

- `trading.risk.RiskLimits.round_trip_cost`: when set, the expected round-trip cost is added to the stop distance in sizing. The loss at the stop *including* costs then stays within the risk limit. The default is 0.
- `trading.engine.DemoTrader.entry_guard` / `on_open`: hooks for a portfolio veto in front of the account risk manager. The default is none.

**New in `jarvis/trading/scalping/`:**

| Module | What it does |
|---|---|
| `tape` | Individual trades with the aggressor side. Bars built from them carry aggressive buy volume, and a minute without trades stays a gap. |
| `microsim` | Execution against the trade tape. See §3. |
| `levels` | Daily open and previous day/week high/low (UTC), from **completed and fully observed** periods only. |
| `strategies` | `ScalpBreakout` (range break on a volume surge), `EmaTrendScalp` (EMA50/EMA200 pullback on volume), `OrderflowLevelScalp` (PVSRA climax at a reference level, confirmed by aggressor delta; no aggressor volume means no signal). All have ATR exits, a time stop, and a reason for every bar. |
| `controller` | Portfolio controller that can only refuse an entry. See §4. |
| `profiles` | `conservative` and `conservative-scalp` (both 1x, 0.5 % risk per trade) and `demo-10x-5pct`, which is **locked**. |
| `splits` | Train / validation / test with embargo; the test can be opened exactly once. |

**Local data:** none for 1m, 3m or 5m. The smallest stored interval is 15m (4 series). There are no trade tapes, order-book or liquidation records.

## 2. Free read-only sources

| Data | Source (public, no key) | Limits / notes | Fit |
|---|---|---|---|
| 1m klines incl. taker buy volume | Binance USD-M `/fapi/v1/klines` | 1,000 bars per request = weight 5 (1,500 = weight 10; measured). IP limit 2,400 weight/min. History from 2019-09 (BTC) / 2019-11 (ETH). 3m and 5m are resampled from 1m. | **History; primary** |
| 1m klines | OKX `/api/v5/market/history-candles` | 100 per request. No taker split. | Cross-check |
| Aggregated trades (aggressor side) | Binance `aggTrades` | 1,000 per request, weight 20 (measured). A time window must be under 1 hour, and time queries reach back **only two days** (error -4166). | Recent short samples only |
| Trades | OKX `history-trades` | 100 per request, paginated backwards, a few months of history | Short samples |
| `historicalTrades` | Binance | Needs an API key | **Excluded** |
| Order book (L2 snapshot) | Binance `depth`, OKX `books` (REST) | A snapshot only; no history is available for free | Recording only |
| Order book stream | Binance/OKX public WebSocket; Coinbase `level2_batch` (Coinbase `level2` needs auth) | Needs a **standing connection** | Needs approval |
| Liquidations | Binance `forceOrder` stream (sampled), OKX public liquidation orders | Recent only, so history must be recorded | Needs approval |
| Bulk trade files | Binance Vision | Licence CC BY-NC-SA, **backtesting only, never the agent** | Research only |

Truly observed order-book and liquidation history therefore only exists from
the day we record it ourselves. Everything older is a **modelled** zone
(`liquidations.modelled_*`), labelled as such, and never counted as an
observation.

## 3. Execution simulation (`microsim`)

No fill at an ideal bar price.

- **Latency:** an order is live only after `latency_ms`.
- **Price:** a market buy pays the ask and a sell gets the bid. The other side of the touch is inferred from the aggressor print plus the spread, then impact is added.
- **Participation:** at most `max_participation` of each later trade, which can leave a partial fill. A market order times out.
- **Limit orders:** they wait in a queue (the size ahead is known or estimated) and fill on prints at the level or when price trades through it. A limit that would cross on arrival is a taker order; with post-only it is rejected.
- **Fees:** maker and taker fees are charged separately.
- **Venue limits and rejections:** a rate limit plus an optional venue-rejection hook.
- **Funding:** perpetual funding comes from the existing paper broker (observed rates per symbol).

**Not modelled:** our own effect on later prices, and reconstructing the order book.

## 4. Risk

- **Profile.** `conservative-scalp` uses 1x leverage and at most 0.5 % of the account per trade, **including** the expected round-trip cost (0.15 % of the price). It has a minimum stop distance of 0.05 %. Its open-risk, daily and drawdown limits are the same as the swing profile's.
- **Locked demo profile.** `demo-10x-5pct` stays locked until the owner approves it in writing for one pre-registered spec hash. It also needs a strategy validated at 10x (the leverage tiers).
- **Controller.** It enforces:
  - entries per day, in total and per strategy;
  - a cooldown after a loss streak;
  - a daily loss limit across all accounts;
  - a cap on concurrent positions and total exposure;
  - no second correlated position in the same direction (an unknown correlation counts as correlated).

## 5. Overfitting guard

- **Splits.** Time-ordered train 60 % / validation 20 % / test 20 %, with embargo gaps between them.
- **One test.** The test period is opened once, for one configuration; a second opening raises an error.
- **Counting.** Every variant counts towards the multiple-testing correction.
- **Full record.** All costs and every negative result are written down.
- **No claim from a backtest.** A good backtest makes no strategy "profitable". At most it qualifies the strategy for its own pre-registered forward paper test.

## 6. Plan and priorities

| Priority | Step | State |
|---|---|---|
| P0 | Foundations: tape, simulator, levels, three candidates, controller, profiles, splits (offline, synthetic tests) | **done** |
| P1 | Download BTC and ETH 1m klines with taker volume from Binance USD-M (OKX cross-check not yet approved) | **approved, done**. See [`trading-scalping-phase1.md`](trading-scalping-phase1.md). |
| P1 | Kline cost model calibrated against short trade samples (`aggTrades`, a few hours) | **approved, done**; to be repeated |
| P2 | Backtest runner: candidates × BTC/ETH × 1m/3m/5m on train/validation, with frozen grids | after P1 |
| P2 | Fill-model check: kline simulation vs tape simulation on the sample windows | after P1 |
| P3 | Optional order-book / liquidation recorder (public WebSocket, time-boxed, local only) | built and offline-tested ([`trading-scalping-recorder.md`](trading-scalping-recorder.md)); a live session **needs approval** |
| P3 | One untouched test per surviving candidate; then, if justified, a pre-registered scalping paper spec | owner decision |
| P4 | SOL, XRP, BNB, LINK, HYPE after a data check | later |

## 7. Data-volume estimate (P1, not yet fetched)

| Item | Estimate |
|---|---|
| Binance 1m bars, 2 years, BTC + ETH | ≈ 2 × 1.05 M bars ≈ **2,176 requests** at 1,000 per page (lower total weight than 1,500 per page), ≈ 315 MB. The earlier figure of 160 MB was wrong. |
| OKX 1m cross-check, 90 days | ≈ 2 × 1,300 requests ≈ 2,600 requests, ≈ 20 MB |
| `aggTrades` samples (24 × 1 h windows per symbol) | several thousand requests; to be sized precisely before the fetch |

Licence: Binance and OKX REST market data is free and public. Binance Vision
bulk files are not used for anything the agent acts on.
