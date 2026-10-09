# Demo trading core

`jarvis/trading/` is the foundation of a multi-asset trading agent. It is
**demo only**: virtual capital, simulated fills, no exchange client, no order
API and no credential path. A guard test
(`tests/unit/trading/test_data_research.py`) fails if the package imports a
network or credential module. Real-money trading would need new code and the
owner's explicit approval; it is not a setting.

The goal is not frequent trading. It is a data-driven system that trades only
when a signal holds up out of sample after costs, and that measures itself
objectively over time.

## Layers

| Module | Role |
|---|---|
| `instruments` | Multi-asset model (crypto, stock, ETF). Only crypto is `demo_tradable`; stocks/ETFs are analysis-only until a later phase. |
| `data` | OHLCV bars with provenance (`source`, `retrieved_at`) and a quality report (gaps, duplicates, out-of-order, impossible bars, zero volume, outliers → `good`/`fair`/`poor`/`unusable`). Unusable data is refused. |
| `indicators` | Causal indicators (SMA, EMA, ATR, RSI, prior range, realized volatility): a value at bar *i* uses bars ≤ *i* only. |
| `research` | Deterministic technical snapshot. Every finding carries its source, the time of the newest bar and the data grade. Dimensions without an approved source (funding, open interest, liquidations, news, macro, ETF flows, on-chain) are listed as missing, never guessed. |
| `strategies` | Rule-based long/short strategies (`SmaCross`, `DonchianBreakout`, `RsiReversion`, plus the do-nothing baseline). A strategy only states a target with stop and take-profit. It never sizes or executes. |
| `risk` | Independent risk manager. Sizing caps the loss at the stop to `risk_per_trade` and allows no leverage. It enforces limits for total open risk, gross exposure, position count, stop distance and price deviation. A daily loss limit stops new entries until the next UTC day; the maximum drawdown triggers the kill switch. Duplicate orders are refused, including after a restart. Exits are always allowed. |
| `paper` | Simulated broker. Fills include a taker fee, adverse slippage and perpetual-style funding. |
| `engine` | One bar loop for backtests **and** the live demo. Decisions are made at a bar's close and filled at the next open. When both stop and target fall inside one bar, the stop is assumed first. Gaps fill at the open. An entry the market gapped through is cancelled. |
| `validation` | Anchored walk-forward. Parameters are chosen in-sample, and only out-of-sample trades count. The edge verdict needs enough trades, a minimum profit factor, positive expectancy after costs and a bootstrap test whose significance level is divided by the number of strategy families compared. **No supported edge → no trade.** |
| `metrics` | Trades, hit rate, profit factor, expectancy, average R, fees, slippage, funding, maximum drawdown, Sharpe, Sortino. |
| `journal` | Every decision, approval, rejection, cancellation, fill, trade and risk event, in memory or SQLite. The SQLite journal also persists the risk state (kill switch, seen order ids). |
| `signals`, `signal_eval` | External signals (e.g. TradingView alerts) as validated, deduplicated evidence, and an event study against random timing. See [External signals](trading-external-signals.md). |
| `leverage` | Simulated futures leverage, gated by tier (see below). Isolated margin with a liquidation price from maintenance margin and a closing-fee reserve. |
| `setups` | The strategy book. Only strategies with a `tradable` walk-forward verdict may open demo trades. Competing setups on one bar are ranked by the strategy's validated out-of-sample expectancy, then its p-value, then the setup's reward-to-risk, and go to the risk manager in that order. |
| `signal_strategy` | External alerts as one more rule-based strategy, validated, ranked and attributed like every other. It acts at the first bar close after an alert *arrived*. |

Defaults (`RiskLimits`):

- 0.5 % risk per trade, 1.5 % total open risk
- 2 % daily loss limit, 10 % maximum drawdown
- 1.0× gross exposure, 3 positions

Defaults (`CostModel`):

- 0.05 % fee per fill
- 5 bps slippage per fill
- 0.01 % funding per 8 h

Lifting the kill switch requires the exact phrase `reset kill switch`.

## Several strategies, one account

- Every strategy is validated and judged on its own: `metrics["by_strategy"]`
  and the journal attribute every decision and trade to its strategy.
- No strategy needs another one's consent. In particular, **no trade requires a
  TradingView signal**. A rule-based strategy trades on its own tested entry
  conditions. A signal-based strategy trades once its own verdict allows it.
  An agreeing alert on another strategy's setup is recorded as
  `confirmed_by`, as information only.
- An open position is managed only by the strategy that opened it, so
  results stay attributable.
- Trade decisions come from pre-defined, tested rules. The guard test also
  forbids model and agent imports (`anthropic`, `openai`, `jarvis.brain`,
  `jarvis.missions`, `jarvis.society`) in this package. A model may comment on
  research later, but it cannot decide a trade.

## Leverage (simulation only)

Leverage never changes what a trade may lose. The risk manager sizes every
position from the stop distance (`risk_per_trade`), exactly as without
leverage. Leverage only sets how much isolated margin a position ties up, and
with it the liquidation price.

| Range | Condition |
|---|---|
| 1–5x | initial demo range |
| up to 10x | the strategy was validated walk-forward **at that leverage** |
| up to 20x | additionally reviewed separately |
| up to 30x | experimental simulation, only with the owner's explicit approval |
| above 30x | never |

- **Fixed per strategy.** A strategy's leverage is a configured, fixed value
  (`StrategyBook.add(..., leverage=LeverageGrant(...))`). A strategy target
  has no leverage field, so neither a rule nor a model can raise it per trade.
- **Stop before liquidation.** The stop must lie before the liquidation
  price and use at most half the distance to it (`liquidation_buffer`).
  Without a gap the stop therefore always fills first. A gap through the
  liquidation price loses the isolated margin.
- **Portfolio limits.** These are added on top of the existing limits:
  - total margin usage (`max_margin_usage`);
  - same-direction risk of correlated positions (`max_correlated_risk`; a
    pair whose correlation is unknown counts as fully correlated).
- **Unclear risk means no trade.** An unknown or too high maintenance margin,
  or unusable data, means no trade.
- **Separate validation.** `validation.compare_leverage` validates each
  leverage level as its own family. The unleveraged variant is always part of
  the comparison, and the significance level is split across the levels.

The liquidation formula is a conservative simplification (lowest maintenance
tier, last price). Real venues use tiered rates and the mark price. Before a
real demo account is used, the venue's own figures replace it.

## Roadmap

Every step is off by default, gets tests, negative controls and a regression
run, and needs the owner's approval where it says so.

1. **Demo core (this package).** Offline, deterministic, no network. ✅
2. **Market data.** First a shortlist of free, keyless sources with licence, limits and reliability; the owner chooses. Then a read-only adapter outside this package that uses the shared HTTP pool and jittered retries (AP-33), caches bars, and hands over a `BarSeries` with provenance. Funding rates and open interest come from public exchange endpoints, if chosen.
3. **Research agent.** The deterministic snapshot plus news and macro sources once approved. Commentary from a society agent (e.g. a Hermes runtime) only on a subscription or local model (`background_policy`), never on a per-token key, with no network or order tools.
4. **Paper-trading service.** A scheduled demo run through the existing scheduler, trading only a strategy whose verdict is `trade`. Read-only REST and CLI views for status, journal and metrics; an owner-only kill switch.
5. **Jarvis integration** (after the Ops core lands):
   - a trading section in the morning briefing (`BriefingExtension`, category `trading`);
   - opt-in notification kinds `trading_signal` / `risk_alert`;
   - spontaneous voice and chat questions through an AppCommand.
   No automatic messages without approval.
6. **Exchange demo account.** A testnet or demo account of an exchange. This needs keys, so the owner must approve it. Still no real money.
7. **Selected altcoins**, one at a time.
8. **Stocks and ETFs:** analyse only, then demo trading, then cross-market comparison with central portfolio risk.

## Not yet covered

- No live data, news or macro data.
- The demo runs one instrument per strategy, and risk is checked across all positions of the account. Portfolio-level optimisation comes with step 8.
- There is no app wiring yet: nothing in `jarvis/trading` runs on boot.
