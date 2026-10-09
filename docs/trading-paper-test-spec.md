# Forward paper-trading test: specification (draft, not active)

A controlled, forward-running **simulation** of two research candidates on
live market data. No real orders, no exchange account, no keys. The job is
off until the owner approves this specification and its activation
separately.

The purpose is to see whether two unconfirmed candidates hold up on data
that did not exist when they were chosen. It is not to find better
parameters. Nothing is optimised during the test.

## 1. Pre-registered strategies (frozen)

| ID | Strategy | Market type | Parameters | Markets |
|---|---|---|---|---|
| **A** | Donchian breakout, daily bars | USD-M perpetual, long and short | `entry_n=20, exit_n=20, stop_atr=2, tp_atr=6, atr_n=14` | see the market rule below |
| **B** | SMA trend, 4h bars | USD-M perpetual, long and short | `fast=20, slow=100, stop_atr=2, tp_atr=4, atr_n=14` | BTC only |

Rule for A's markets, decided by objective criteria and **not** by past
results:

- the market's median daily volume is at least 200 million USD;
- it has at least 4 years of perpetual history.

Today that gives **BTC, ETH, SOL, XRP and BNB**. LINK and HBAR have too
little liquidity for this tier, and HYPE has too little history. A market
does not join or leave during the test.

The configuration is written to a pre-registration file, and its hash is
recorded before the start. The runner refuses to run if the code or the
parameters differ from it.

## 2. Execution model and costs

- **Signals:** at each bar close (UTC), computed from closed bars only. The
  source is Binance USD-M bars. OKX is the cross-check; if the two
  disagree, or the data is stale, there is no new entry.
- **Fills:** simulated at the next bar's open, the same rules as in the
  backtests (stop before target inside a bar; gaps fill at the open).
  Each simulated fill also records the market price seen when the order
  would have been placed, to measure model slippage.
- **Fees:** 0.05 % taker per fill.
- **Slippage by liquidity tier:** 5 bps (BTC, ETH, SOL) and 8 bps (XRP, BNB).
- **Funding:** the venue's observed payments, charged at their real times.
- **Account:** 10 000 USD virtual. The candidates share one account and the
  one risk manager.

## 3. Risk (unchanged)

- 1x leverage only. Higher tiers need a separate validation at that
  leverage.
- Risk per trade ≤ 0.5 % of equity at the stop; total open risk ≤ 1.5 %.
- Gross exposure ≤ 1.0×; at most 3 positions.
- Correlated same-direction risk ≤ 1 %; an unknown correlation counts as
  full.
- Daily loss limit 2 % (no new entries until the next UTC day).
- Kill switch at 10 % drawdown. It closes all positions and only the owner
  can lift it, with the exact phrase.

## 4. Duration and expected trade counts

- The first planned observation phase lasts **at least 6 months**.
- Expected trade counts, from the backtests:
  - **A:** about 10–12 trades per market per year, so about 50–60 a year
    across five markets. **40 trades take about 8–10 months.**
  - **B:** about 20 trades a year. **40 trades take about 2 years.** B will
    not be decidable after 6 months; it then shows only whether it behaves
    within its backtest range.
- Evaluation per strategy happens **only after ≥ 40 simulated trades**.
  Before that, results are reported but not judged.

## 5. Success criteria (fixed before the start)

A strategy passes when, at its evaluation point:

1. net profit factor ≥ 1.2 after all costs;
2. average R per trade > 0;
3. one-sided bootstrap test of the trade R values with p ≤ 0.025 (0.05
   split across the two strategies);
4. maximum drawdown ≤ 10 % (the kill switch did not trigger);
5. realised slippage is not worse than twice the modelled slippage.

Passing means the strategy may be proposed for a longer paper phase. It
does not mean trading with money.

## 6. Abort rules (fixed before the start)

- The kill switch triggers: the test stops for all strategies. The cause is
  reported, and there is no automatic restart.
- **Futility:** after 20 trades, a strategy with profit factor < 0.7 is
  stopped. The others continue.
- **Data:** if the primary and backup sources disagree, or data is stale
  for more than 2 bars, new entries pause until the data is good again.
  This is reported.
- Any change to code, parameters or markets ends the test. A new test then
  needs a new pre-registration and starts from zero.

## 7. Journal and reporting

- Every decision, approval, rejection, cancellation, fill, trade, funding
  payment and risk event goes to the SQLite journal. Daily equity
  snapshots are kept.
- A weekly report covers each strategy and market: trades, net return,
  profit factor, average R, drawdown, fees, funding, slippage against the
  model, and the result against the backtest range.
- Telegram summaries or a briefing section are possible only after the
  owner approves them (the briefing needs the Ops core, PR #499).

## 8. Technical steps before activation

1. **Persistent paper account:** save and restore the broker state
   (positions, cash, pending orders). The risk state is already persisted
   in the journal.
2. **Pre-registration:** a frozen configuration file and a hash check in
   the runner.
3. **Paper runner:** a scheduled job in the existing Jarvis scheduler, a
   few minutes after each 4h and daily close.
   - It updates closed bars incrementally through the adapters and the
     cache, and cross-checks the sources.
   - It feeds the existing engine and strategy book.
   - It uses periodic polling only, no standing connection. It is off by
     default and needs approval to start.
4. **Read-only views:** REST endpoints and CLI for status, journal and
   metrics, plus an owner-only kill switch. The routes are excluded from
   voice actions.
5. **Weekly report generator.**
6. **Tests:**
   - restart without double orders;
   - a missed bar is caught up exactly once;
   - stale or conflicting data pauses entries;
   - a hash mismatch blocks the start.

## 9. Later additions

Scalping strategies validated on trade-level simulation
(`docs/trading-scalping.md`) can join the same framework as further
strategies in the book. Each gets its own pre-registration, success
criteria and test count, and is evaluated separately.
