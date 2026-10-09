# Trading research plan: data, markets, timeframes

The goal is a self-analysing multi-asset agent, not a single-market bot. Every
step is demo-only and read-only. A data download is planned first (requests,
time, storage) and approved before it runs. No phase activates a standing
connection or the paper-trading job.

## Building blocks

- **Data:**
  - `jarvis/market_data` adapters with a cache and cross-venue checks;
  - `backfill.plan` states the size of every download before it runs.
- **Timeframes:** `jarvis/trading/timeframes`.
  - Supported: 15m, 1h, 4h, 1d, and 5m prepared.
  - Higher bars are built only from complete lower bars of the same source,
    and are visible only after they close.
  - A higher-timeframe confirmation is a strategy family of its own and must
    beat the unfiltered strategy.
- **Universe:** `jarvis/trading/instruments`, `availability`.
  - Research instruments: SOL, XRP, BNB, LINK, HYPE, HBAR. They are
    analysis-only until validated.
  - Gold in three distinct forms.
  - Instruments enter a test only after their listing plus a warm-up, and
    delisted instruments stay in the record.
- **Validation:** walk-forward with an untouched final holdout.
  - Fold consistency is required.
  - The significance level is divided by every coin × timeframe × family
    combination compared.
  - Leverage variants are validated separately against the unleveraged
    baseline.

## Phases

1. **Pilot (done).** BTC and ETH, two years on 1h/4h/1d and one year on 15m.
   - Sources: Bybit perpetuals (primary, with funding and open interest), OKX
     perpetuals (backup), Binance spot (taker volume for CVD), Coinbase daily
     spot (reference).
   - Size: about 830 requests, 9 minutes, 30 MB.
2. **Longer BTC/ETH history.**
   - Binance spot back to 2017.
   - Bybit and OKX perpetuals back to their listings.
   - Then the first reliable strategy and walk-forward comparisons, leveraged
     and unleveraged.
3. **Altcoins.** SOL, XRP, BNB, LINK, HYPE, HBAR.
   - First check per venue: listing date (HYPE only began trading in late
     November 2024), depth, spread, funding history and data quality.
   - Then history and tests from each real listing onwards.
4. **Gold.** Spot XAU/USD, gold futures and gold-backed tokens stay separate:
   - Exchange gold perpetuals are recent: one major venue launched one in
     January 2026.
   - Another venue renamed a token-indexed perpetual and changed its price
     index in January 2026, so its history must be split at that date.
   - Spot gold trades 24/5, the perpetuals 24/7.
   - Free spot history exists from broker data sources, whose licence terms
     must be checked first.
5. **Dynamic market selection.** Rank instruments by:
   - liquidity, volume, volatility and spread;
   - data quality;
   - demonstrated strategy fit, using only information available at each
     point in time.

## What the pilot showed (preliminary)

Over two years, after costs, with a 20 % final holdout and a significance
level corrected for 2 coins × 3 timeframes, **no strategy family qualified**:

- On 1h, all families lose after fees and slippage. A 4h trend filter did not
  help.
- On 1d, two years give too few trades to judge.
- One 4h trend-following candidate looked positive out of sample and in the
  holdout, but is not significant on this little data. It is a candidate for
  phase 2, not a result.

The sources agree closely: Bybit and OKX perpetual closes differ by about
0.01 % (median), and Binance and Coinbase spot by about 0.04 % (USDT vs. USD).

## Phase 2 results (BTC and ETH, longer history)

Data:

- **Perpetuals:** Binance USD-M BTC/ETH, 2020-10 to 2026-10, 1h/4h/1d, with
  observed funding of the same venue (6 570 payments each). OKX perpetuals
  cover the same span as a cross-check.
- **Spot:** Binance BTC/ETH, 2018-10 to 2026-10, 1h/4h/1d, plus 3 years of
  15m, with taker volume. The 1h series has 70 missing hours (venue
  maintenance); they are documented and not filled.
- **Spot and perpetual studies are separate.** Spot is long only, has spot
  fees and no funding. Perpetuals are long and short with observed funding.

Results: **66 combinations** (2 coins × 3 timeframes × 3–5 families, plus 1x
and 3x leverage on perpetuals), walk-forward with a 20 % untouched holdout
and a significance level corrected for every combination. **None
qualified.**

- **1h:** every family loses after fees, slippage and funding, on spot and
  perpetuals. A 4h trend filter does not change that.
- **RSI mean reversion:** loses on every coin, timeframe and market (profit
  factor 0.52–0.72).
- **Strongest candidate:** BTC 4h SMA trend on perpetuals.
  - Out-of-sample profit factor 1.52, +0.27 R per trade, 106 trades.
  - Positive in every year from 2021 to 2025.
  - Holdout profit factor 2.09, +8.8 %, 2.1 % drawdown.
  - Not significant after correction (p = 0.023 against a required 0.0014).
- **Daily breakouts:** positive out of sample on BTC and ETH spot (profit
  factor about 2.2), but they lose in 2022 and 2025. Only the ETH variant
  also held in the holdout.
- **Leverage:** 3x changed results in only 7 of 22 cases, and never turned a
  loser into a winner. With a fixed risk per trade, leverage changes margin,
  not edge.

## Coverage of further markets by the existing adapters

| Market | Binance spot | Binance perpetual since | OKX perpetual since | Coinbase |
|---|---|---|---|---|
| SOL | yes | 2020-09 | 2021-01 | yes |
| XRP | yes | 2020-01 | 2019-11 | yes |
| BNB | yes | 2020-02 | 2022-12 | yes |
| LINK | yes | 2020-01 | 2020-02 | yes |
| HYPE | yes | 2025-05 | 2025-02 | yes |
| HBAR | yes | 2021-03 | 2023-08 | yes |
| Gold perpetual (XAU) | – | 2025-12 | 2025-04 (token index until 2026-01) | – |

Dates are the venues' own metadata. The first bar a venue actually serves
is authoritative.

- **HYPE** began trading on 2024-11-29, but on these venues only in
  2025-02/05. Its first months exist on its native venue, which would need
  its own adapter.
- **Spot gold** (XAU/USD) is not covered by any adapter. It needs a separate
  source whose licence is checked first.

## Phase 3 results (SOL, XRP, BNB, LINK, HYPE, HBAR)

Data (one-time, keyless, read-only): 1 495 requests, 107 MB.

- **Binance USD-M perpetuals** with observed funding of the same venue.
- **Binance spot.**
- **OKX perpetuals** as a cross-check.
- **History by coin:**
  - SOL, XRP, BNB and LINK: 6 years.
  - HBAR: 5.6 years on perpetuals.
  - HYPE: 1.4 years on perpetuals (since 2025-05-30). Spot history on the
    venue was only 15 days, which is too little to test.
- **Slippage by liquidity:** 5–20 bps from each coin's median daily volume.

Two parts, all with fees, slippage, funding and the unchanged risk limits:

- **A: frozen parameters.** The BTC 4h trend, 4h breakout and 1d breakout
  strategies keep the parameters chosen on BTC data *before* its holdout.
  They were never fitted on these coins, so the whole period is out of
  sample.
  - Each coin is reported in full, before its last 20 % and in its last
    20 %, and per year.
  - 54 tests, significance level 0.05 / 54.
- **B: walk-forward per coin.** The same small grids, with a 20 % untouched
  holdout. 54 tests.

Results:

- **No combination qualified** in A or B. The lowest p-value was 0.0012
  (SOL spot, 1d breakout) against a required 0.00093.
- **The BTC 4h trend does not transfer.** It is positive on LINK (profit
  factor 1.17, holdout 1.45) and slightly on XRP, flat on SOL, and negative
  on BNB, HBAR and HYPE. It stays an unconfirmed research candidate.
- **4h breakouts are mixed.**
  - SOL is positive in 6 of 7 years.
  - LINK and HBAR hit the drawdown kill switch early, and the backtest
    then stops trading. Their numbers reflect the safety stop.
- **Daily breakouts (frozen 20/20)** are the most consistent family across
  markets.
  - Full-period profit factor 1.3–2.7 on SOL, BNB, XRP (spot) and HBAR, as
    well as on BTC and ETH spot in phase 2.
  - The recent 20 % is mixed: BNB stays positive, SOL turns negative.
  - With only 40–70 trades per coin, none is significant on its own.
- **HYPE:** negative or insufficient on every strategy. HBAR: only the daily
  breakout is positive (profit factor 1.33–1.55, recent period flat).
- **Leverage (3x):** results are nearly identical to 1x. It adds no edge.

Tests so far: phase 2 had 66 combinations, phase 3 had 54 (A) and 54 (B),
**174 in total**. Every one is reported, including the losing ones.
