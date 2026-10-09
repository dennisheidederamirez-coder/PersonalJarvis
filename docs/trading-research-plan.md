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
