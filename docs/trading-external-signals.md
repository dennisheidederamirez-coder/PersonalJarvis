# External signals (TradingView alerts)

Indicators the owner already uses on TradingView can feed the trading agent
as **evidence, never as orders**. A received alert is validated, recorded
with timestamps and deduplicated. It is then measured against independent
market data. It only influences demo trading after it has shown out of sample
that it carries information beyond random timing.

## Ground rules

- No automated TradingView login, no scraping. TradingView's terms forbid
  automated data collection, and unofficial endpoints break without notice.
  Data from TradingView arrives only through alerts the owner sets up
  themselves.
- No copying of protected or invite-only scripts and no attempt to bypass
  access limits. Indicators with a public definition (EMA, daily open, PVSRA
  candle classes, support/resistance) are implemented independently in
  `jarvis/trading/indicators.py`. Closed indicators are used only through
  their own alerts, as a black box.
- No paid plan is booked by Jarvis. Webhook alerts need a paid TradingView
  plan and two-factor authentication on the account; that is the owner's
  decision.

## What TradingView can deliver

| Script type | Source visible | Usable by | How Jarvis can use it |
|---|---|---|---|
| Open-source | yes | everyone | Re-implement the published definition independently, backtest on years of history, and cross-check against its alerts. |
| Protected | no | everyone | Only the values the script exposes to alerts (alert conditions, `{{plot_N}}`); evaluate as a black box. |
| Invite-only | no | invited users | Same as protected, and only while the owner's access lasts. |

Alert transport: a TradingView alert POSTs its message to a webhook URL.
- Only ports 80/443 are accepted, IPv4 only.
- The request is cancelled after 3 seconds.
- Requests come from four published source IPs.
- A JSON message is sent as `application/json`.

The message is free text with placeholders (`{{ticker}}`, `{{exchange}}`,
`{{interval}}`, `{{time}}` = bar open, `{{timenow}}` = firing time,
`{{close}}`, `{{plot_N}}`). TradingView cannot set custom headers, so the
connection secret travels in the JSON body.

## Pipeline

1. **Alert** (set up by the owner) with the JSON message documented in
   `jarvis/trading/signals.py`, preferably "once per bar close".
2. **Receiver** (later; needs a decision on public reachability).
   - It sits on top of the existing routine webhook ingress
     (`jarvis/tasks/webhook_auth.py`, `/hooks/{task_id}`) with a TradingView
     provider: body token compared in constant time, the published source
     IPs only, a size limit, and an answer well under 3 seconds (record
     only, no work in the request).
   - The Mac is not reachable from the internet, so this needs either a
     tunnel exposing only that path or a small relay that queues alerts for
     Jarvis to pull.
   - Without webhooks, alert e-mails into the already connected mailbox are
     a fallback through the same parser.
3. **`signals.parse_tradingview`**: strict validation (version, direction,
   interval, timestamps, price, names). Bad payloads are refused, and the
   token is never stored.
4. **`signals.SignalLog`**: one signal per indicator, signal, symbol,
   interval and bar. Repeated deliveries are ignored.
5. **`signal_eval.study`**: an event study against an independent price
   series.
   - Entry is at the next bar's open, with returns after several horizons
     minus costs.
   - It is compared with random entry times of the same count and
     directions. A Bonferroni correction is applied across horizons, and a
     minimum event count is required.
   - It also counts signals that cannot be matched to a bar or whose price
     disagrees with the independent data.
6. **As a strategy of its own**: `signal_strategy.SignalStrategy` turns a
   signal family into an ordinary strategy with ATR stop and target, an
   exit on an opposite signal and a maximum holding time.
   - It goes through the same walk-forward validation and only trades once
     its own verdict allows it; random alerts earn no permission.
   - Signals are optional for every other strategy: an agreeing alert is
     only recorded as `confirmed_by`.
   - Each strategy is evaluated separately, so the record shows over time
     whether the alerts add anything.

## Open decisions

- Whether to use webhooks at all: they need a paid plan and 2FA.
- If so, how the receiver becomes reachable: a tunnel limited to one path,
  or a relay.
- Which of the owner's indicators expose alert conditions. Their names and
  versions stay in the owner's local notes, not in this repository.
