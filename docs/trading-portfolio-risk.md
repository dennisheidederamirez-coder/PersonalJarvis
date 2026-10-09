# Portfolio risk manager (concept) and risk profiles

Status: concept. The running paper test keeps one virtual account per
candidate. Nothing here changes those accounts.

## Why a portfolio layer

The per-account risk manager already enforces:

- a stop-based size per trade (0.5 % of equity);
- caps on open risk, gross exposure and positions;
- a correlated-risk cap;
- a daily loss halt and a drawdown kill switch.

Once several strategies, markets and later scalping run together, three
things need a view above the single account:

- correlated positions across strategies;
- loss limits that hold for the whole virtual capital;
- the total load on that capital.

## Design

A `PortfolioRiskManager` sits **in front of** the existing per-account
`RiskManager`. It never loosens an account limit; it can only refuse more.

- **Inputs on every entry request:** the strategy's account, all open
  positions across all accounts (symbol, side, size, stop, mark), recent
  returns per symbol for the correlation estimate, and every account's
  equity and peak.
- **Capital buckets:** each strategy gets a fixed share of the total
  virtual capital (its risk budget). A strategy can only use its own
  bucket, so a busy strategy cannot crowd out the others. Buckets are part
  of the pre-registered spec.
- **Portfolio limits** (on top of the account limits):
  - total open risk at the stops across all accounts ≤ a portfolio cap
    (e.g. 2 % of total capital);
  - correlated same-direction risk ≤ a cap, using an estimated correlation
    matrix. The estimate uses rolling 90-day daily returns, shrunk towards
    1.0. With too little data the correlation counts as 1.0, so unknown
    means correlated;
  - net exposure per asset across strategies (e.g. BTC long from A and
    short from B net out for exposure but not for risk);
  - the gross exposure and margin of all accounts together.
- **Portfolio loss limits:** a daily loss limit and a drawdown kill switch
  on the **sum** of all accounts. When the portfolio switch fires, every
  account stops opening and every position is closed. It is lifted only by
  the owner's exact phrase. Account switches stay as they are.
- **Competing entries in the same bar:** a defined, pre-registered order of
  precedence:
  1. the strategy's validated edge;
  2. the setup's reward-to-risk;
  3. instrument liquidity;
  4. the strategy id.

  It is never arrival order.
- **Shadow record:** every entry the portfolio layer refuses is journaled
  with its reason and simulated as a "shadow trade". A blocked strategy's
  record stays measurable, and the evaluation of each strategy stays
  independent of the others.
- **Reporting:** the portfolio total, each bucket, the correlation
  snapshot, the limit usage in percent, and the reasons for refused
  entries.

## Risk profiles

A risk profile is a named, frozen set of limits. The default stays as it
is: **"conservative": 1x leverage, at most 0.5 % risk per trade**, 1.5 %
open risk, 2 % daily loss limit, 10 % drawdown kill switch.

- Every other profile, for example a separate scalping profile with its own
  trades-per-day cap, cooldown, data-age and latency limits, is
  **explicitly approved by the owner**. It is bound to one strategy in one
  pre-registered spec, with its own hash.
- A profile can tighten anything. It can loosen only what the leverage
  tiers allow:
  - more than 5x only for a strategy validated at that leverage;
  - more than 10x only after a separate review;
  - more than 20x only experimentally and with explicit approval;
  - more than 30x never.
- No profile is selected automatically, and nothing raises risk at
  runtime: not a strategy, not a confidence score, not a model.

## Implementation order (when approved)

1. Correlation estimator (rolling returns, shrinkage, "unknown means 1.0").
2. `PortfolioRiskManager` with buckets, portfolio limits and the precedence
   rule. Tests cover synchronous breakouts on five correlated markets, an A
   and B overlap on BTC, and the portfolio kill switch.
3. Shadow trades in the journal and in the report.
4. A portfolio view in the daily report.
5. Only then: a second pre-registered spec that runs candidates in one
   portfolio, next to (not instead of) the separate accounts.
