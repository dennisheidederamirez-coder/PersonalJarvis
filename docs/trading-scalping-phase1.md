# Scalping research — phase 1 results (2026-10-10)

Demo research only. Nothing here is a trading recommendation. The running
forward paper test (1d breakout and 4h trend) was not touched.

## 1. Data collected

| Item | Result |
|---|---|
| Source | Binance USD-M REST, public, no key; `/fapi/v1/klines` (1m), `/fapi/v1/fundingRate` |
| Window | 2024-10-10 00:00 → 2026-10-10 00:00 UTC (2 years) |
| Bars | BTCUSDT 1,051,200 and ETHUSDT 1,051,200. **No gaps.** Taker buy volume in every bar. |
| Funding | 2,190 observed 8h rates per symbol |
| Requests | 2,176 (as planned), in 14.5 min, 0 errors; ≈ 1,000 of 2,400 weight/min used |
| Storage | 289 MB, in a separate local file (not the paper test's store) |
| Quality | 35 of 48 monthly slices graded `good`; 13 `fair` because of "extreme moves" (e.g. October 2025). |

On 1m data these are expected market moves, not gaps. No independent
cross-check was made: the OKX cross-check was not part of this approval.

Measured API facts (2026-10):

- `klines` with 1,000 rows costs weight 5; with 1,500 rows, weight 10.
- `aggTrades` costs weight 20, and a time query reaches back **two days only** (error -4166).
- `historicalTrades` needs an API key, so it is not used.

Under the API terms, personal research use of the REST data is fine. The
Binance Vision bulk files (CC BY-NC-SA) were not used.

## 2. Trade samples and cost calibration

These are 12 windows of 20 min each on 2026-10-09: for each symbol, the 2
quietest, 2 median and 2 busiest hours by 1m volume. The run used **116
requests** (budget 600) and collected ≈ 109,000 aggregated trades, 11 MB.
Every window is complete.

The samples are necessarily recent, because of the two-day limit. They lie in
the test period, but they only calibrate costs and are not used to select a
strategy.

| Measured | BTCUSDT | ETHUSDT |
|---|---|---|
| Effective spread (median) | ≈ 1 tick (0.01 bp) | ≈ 1 tick (0.04 bp) |
| Price drift during 150 ms latency, median / p90 | 0.01–0.23 / 0.6–1.2 bp | 0.04–0.24 / 0.6–1.1 bp |
| Price drift during 1 s latency, median / p90 | 0.01–0.40 / 0.7–1.5 bp | 0.04–0.44 / 0.9–1.6 bp |

**Not yet measurable:**

- **Price impact:** the ≈ 1 bp per order is the simulator's *assumed* impact.
- **Fill rate:** without order-book depth, the participation model is deliberately pessimistic. A 10,000 USD market order often "fills" only partly within 5 s, whereas in reality the resting book would absorb it at once. The recorder (see [`trading-scalping-recorder.md`](trading-scalping-recorder.md)) would fix both.

**Cost conclusion:**

- For a 10,000 USD taker round trip at the base fee tier, the estimate is ≈ 0.10 % fees + ≈ 0.00–0.03 % spread and drift ≈ **0.10–0.13 %**.
- The 0.15 % research assumption is therefore conservative for normal hours on BTC and ETH.
- One day cannot show stress or news hours.
- **Fees dominate the cost.** Spread and latency barely matter at this size.

## 3. Strategy tests (train and validation only)

The plan was pre-registered before any download (`study_plan.json`, sha256
`f67c5d46…`):

- **Splits:** train 2024-10-10 → 2025-12-21; validation 2025-12-22 → 2026-05-15; test 2026-05-16 → 2026-10-10. There is a 1-day embargo between each pair.
- **Test period:** never loaded. The study reads bars only up to the end of validation.
- **Configurations:** 3 candidates with library defaults (no tuning) × 2 symbols × 1m/3m/5m = 18 configurations, each at 0.15 / 0.25 / 0.40 % round-trip cost.
- **Risk:** the conservative scalping profile (1x, 0.5 % risk per trade including costs) and the portfolio controller.
- **Pass rule:** in validation at 0.25 %, at least 30 trades, PF ≥ 1.2, and p < 0.05 / 18 = 0.0028; training must also be profitable.

**Result: 0 of 18 configurations pass.** All 108 runs (18 × 3 costs × 2
periods) hit the 10 % kill switch after 25–91 trades. The kill switch stops a
run early, so each result covers the first weeks of its period, not all of it.

| Best runs at 0.15 % (the cheapest level) | PF train | PF validation |
|---|---|---|
| ETH 5m EMA trend | 0.48 | 0.38 |
| ETH 5m breakout | 0.43 | 0.43 |
| ETH 3m order flow at levels | 0.40 | 0.32 |
| BTC 5m EMA trend | 0.47 | 0.19 |
| BTC 1m (all three) | ≤ 0.13 | ≤ 0.06 |

Every run at 0.25 % and 0.40 % is worse. The full table is in `study_phase1.json`.

## 4. Why: targets smaller than costs

Median ATR in the training period, against the 0.15 % round-trip cost:

| | 1m | 3m | 5m | 15m | 1h |
|---|---|---|---|---|---|
| BTC ATR | 0.056 % | 0.11 % | 0.15 % | 0.29 % | 0.62 % |
| 2 × ATR target / cost | **0.74** | 1.47 | 2.0 | 3.8 | 8.2 |
| ETH ATR | 0.10 % | 0.19 % | 0.26 % | 0.48 % | 1.03 % |
| 2 × ATR target / cost | 1.35 | 2.56 | 3.43 | 6.4 | 13.7 |

- **1m BTC:** a full take-profit is *smaller* than the round-trip cost. A win of 2 ATR still loses money.
- **3m and 5m:** the stop (1–1.5 ATR) plus costs costs more than the target brings in at the hit rates seen (15–38 %).
- **Taker fees:** at 0.05 % per fill they alone make ATR-scaled taker scalping on 1–5m structurally unprofitable here.

The finding does **not** say "order flow or levels carry no information". It
says that **with taker fees and these exits** no candidate covers its costs.

## 5. Consequences (proposals, not done)

1. **Maker execution** (0.02 % fee per fill, a post-only entry) would roughly halve fees. It brings fill and adverse-selection risk, which can only be measured honestly with order-book data (recorder).
2. **Larger moves:** 15m as the shortest frame, or targets independent of ATR, with costs counted before a trade is taken (a "target ≥ k × cost" filter).
3. **No parameter search** on these results: the test period stays untouched, and any new variant enters a newly pre-registered phase that counts towards multiple testing.

## 6. Reproduction

| What | Where |
|---|---|
| Data and records | `~/pj-trading-data/scalp/`: `market_1m.sqlite`, `trades_samples.sqlite`, `study_plan.json` (read-only), `study_phase1.json`, `fetch_1m_log.json`, `trade_samples_log.json` |
| Code | `jarvis/trading/scalping/` (`study.py`, `calibrate.py`), `jarvis/market_data/trades.py` |
