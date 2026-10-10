# Scalping research — phase 2: limit orders, larger moves, a recorder (2026-10-10)

Demo research only. The forward paper test (1d and 4h) was not touched.
The phase-1 results (`study_phase1.json`, 0 of 18 passed) are kept unchanged.

## 1. What was built

| Module | Purpose |
|---|---|
| `scalping/execsim.py` | Bar-level execution with **separate maker and taker models** (see §2) |
| `scalping/setups_v2.py` | Three candidate families in maker and taker form, with stops and targets scaled by the last **closed 15m** ATR and holds of at most 30 min. Session and volatility filters are optional. |
| `scalping/bookcal.py` | Calibration from recorded book and trades (see §4) |
| `market_data/recorder.py` | The 60-minute recorder (see §5). **Built and tested offline, never started.** |

## 2. Execution model (modelled, not observed)

- **Taker:** a signal at a bar close executes at the NEXT bar's open, plus a
  latency of 0 or 1 extra bar, half the spread, slippage and the taker fee
  (0.05 %).
- **Maker:**
  - **Fill rule:** the order rests for 5 min and fills only when price trades **through** the limit by a scenario threshold; a touch is not a fill.
  - **Partial fills:** a penetration band produces them, and the remainder is cancelled. Unfilled orders expire and are counted.
  - **Price and cost:** the fill is at the limit, minus an adverse-selection haircut, plus the maker fee (0.02 %).
  - **Adverse selection:** the price move after a fill is compared with the move after every placement.
- **Exits:**
  - **Target:** a maker exit, only in a later bar.
  - **Stop:** a stop-market exit; on a gap it fills at the open.
  - **Time stop:** a taker exit at the next open.
  - **Both in one bar:** the stop counts.
- **Risk:** 1x, 0.5 % of equity at risk **including** the round-trip cost.
  The position is capped at 1x of the fill price and 1 % of the bar's volume,
  and the portfolio controller sits in front.
- **Minimum move:** a setup is skipped when its target is under 3 × the
  scenario's round-trip cost.

| Scenario | Spread | Taker slippage | Trade-through | Partial band | Adverse | Extra latency |
|---|---|---|---|---|---|---|
| optimistic | 0.2 bp | 0.5 bp | 0.5 bp | none | 0 | 0 |
| base | 0.5 bp | 1.5 bp | 1.5 bp | 2 bp | 0.5 bp | 0 |
| harsh | 1 bp | 4 bp | 3 bp | 4 bp | 2 bp | 1 bar |

These scenarios bracket the phase-1 trade samples: a spread of about 1 tick
and a latency drift of 0.2–1.5 bp. They are still assumptions until the
recorder measures maker fill chances and adverse selection.

**How biased is the model?** Random entries at zero cost lose only
0.00–0.04 R per trade (train, BTC). That is the price of the worst-case
rules: stop before target in an ambiguous bar, and no touch fills. The model
is conservative, but not strongly so.

## 3. Strategy development (train data only)

The plan was pre-registered before any run (`study_plan_phase2.json`,
sha256 `47ce0a69…`):

- **Grid:** 64 variants = (breakout, trend pullback, order flow ×
  {continuation, absorption}) × 1m/5m × maker/taker × target {1.0, 1.5} ×
  15m ATR × filter {none, session + volatility}. The stop is 0.75 × 15m ATR.
- **Data:** BTCUSDT and ETHUSDT, base scenario, train period only. The code
  reads bars only up to 2025-12-21.
- **Selection:** for each family × order type, the best pooled average R
  among variants with ≥ 200 trades and PF > 1.0.

**Result: no variant qualifies, so nothing goes to validation.** This
follows the pre-registered rule: an empty slot is not filled. The validation
and test periods stay unused.

| Family | Best pooled train PF (n) | Avg R range across its 16–32 variants |
|---|---|---|
| Breakout | 0.59 (567) | −0.33 … −0.21 |
| Trend pullback (EMA50/200 + aggressor volume) | 0.58 (369) | −0.28 … −0.20 |
| Order flow, continuation | 0.63 (1,570) | −0.29 … −0.17 |
| Order flow, absorption | 1.12 (57) | −0.32 … −0.02 |

The full table, with per-symbol PF, R, drawdown, fees, order counts (placed,
filled, partial, unfilled) and bootstrap intervals, is in
`study_phase2_dev.json`.

### Why — a diagnostic (train, BTC; not used for selection)

| Variant | Zero cost: avg R [95 % CI] | Base: avg R [95 % CI] |
|---|---|---|
| 1m breakout, maker | −0.051 [−0.078, −0.024] | −0.305 [−0.330, −0.277] |
| 1m breakout, taker | −0.023 [−0.051, 0.005] | −0.245 [−0.278, −0.215] |
| 1m order flow continuation, taker | +0.001 [−0.028, 0.031] | −0.241 [−0.291, −0.199] |
| 5m breakout, taker | −0.075 [−0.110, −0.043] | −0.289 [−0.341, −0.237] |
| 5m trend pullback, maker | +0.015 [−0.048, 0.088] | −0.234 [−0.311, −0.157] |
| 5m order flow absorption, maker | +0.060 [−0.074, 0.191] | −0.249 [−0.415, −0.082] |
| Random entries, maker / taker (1m) | −0.036 / +0.001 | — |

- **Before costs** the signals are indistinguishable from random entries (every interval includes 0, or lies below it).
- **After costs** they lose about 0.25 R per trade.
- **The limit is the signal, not the execution:** even maker fees (0.02 %) cannot rescue a signal with no gross edge.

### Market phases, volatility, liquidity, hours (exploratory, train)

The best variant per family × order type was broken down by session
(Asia / Europe / US / late), 15m-ATR volatility tercile, bar-volume tercile
and 15m trend phase (with / against / flat). This is in
`study_phase2_regimes.json`.

- **Nothing robust:** no bucket with a meaningful sample has an interval above 0.
- **One possible lead:** order-flow absorption with a limit entry in high volatility shows +0.24 R [−0.02, 0.53], but on only **26 trades**. It is a hypothesis for a new pre-registered test, not a result.

## 4. Calibration from the order book (ready, not yet run)

`bookcal.py` turns a recording into:

- **Observed:** spread (median, p90, max) and resting notional within 1, 5 and 10 bp of the mid.
- **Observed book, modelled consumption:** the cost of a market order of a given notional, walked through each recorded snapshot.
- **Modelled:** fill chances for post-only orders at the best bid and ask.
  - The order joins the **back** of the displayed queue and fills only from recorded trades, so this is not an observed fill.
  - It yields the fill and partial rates, the time to the first fill, and adverse selection: the mid move after a fill against the move after any placement.

The results will replace the scenario assumptions in §2 (spread, maker
trade-through, adverse haircut). Hidden size and cancellations ahead in the
queue remain unknown.

## 5. Recorder (built, offline-tested, NOT started)

See [`trading-scalping-recorder.md`](trading-scalping-recorder.md). Start
command, once the owner approves:

```
python -m jarvis.market_data.recorder --out <dir>/recording_1.sqlite \
  --minutes 60 --stop-file <dir>/STOP_RECORDER --owner-approved
```

Without `--owner-approved` the command prints its plan and exits with code 2
without connecting. This was verified on 2026-10-10, and no file was created.

## 6. Open points

1. Run the recorder once, with owner approval, then apply `bookcal` and re-derive the scenarios.
2. Decide whether to pre-register a narrow follow-up hypothesis (e.g. absorption in high volatility, maker). It would carry a new multiple-testing count. Validation data is still unused, and the test period is untouched.
3. Every family tested so far lacks a gross edge at 1–5 minute horizons on BTC and ETH. More parameter search on the same family would be overfitting.
