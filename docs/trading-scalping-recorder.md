# Order-book and liquidation recorder — concept (not built, not running)

Status: **concept only.** No WebSocket connection and no background recorder
is active. Each recording session needs the owner's explicit start.

## Why a recorder

No free source offers order-book or liquidation **history**. What we can
call real is only what we record ourselves, from the moment we start. Older
"liquidity" is always a **modelled** zone.

## Sources (public, read-only, no key)

| Data | Binance USD-M | OKX (perpetual swaps) |
|---|---|---|
| Book | `<sym>@depth@100ms` diff stream, plus a REST `/fapi/v1/depth?limit=1000` snapshot to sync | `books` channel (400 levels) with checksum, or `books5` |
| Trades | `<sym>@aggTrade` | `trades` |
| Liquidations | `<sym>@forceOrder`: **only the largest per symbol per second** | `liquidation-orders`: at most one update per contract per second |

Neither venue publishes every liquidation. Recorded liquidation events are
therefore a **lower bound** and are labelled with that coverage
(`trading.liquidations.FEED_COVERAGE`).

## Data model (local SQLite, separate file per session)

| Table | Contents |
|---|---|
| `book_top` | ts, venue, symbol, side, level, price, size. The top 20 levels sampled every 1 s (configurable down to 250 ms). |
| `book_events` | ts, venue, symbol, kind (`snapshot` / `resync` / `sequence_gap` / `checksum_fail`). Every break is visible. |
| `trades` | ts, venue, symbol, price, qty, buyer_aggressor (same as `md_trades`) |
| `liquidations` | ts, venue, symbol, side, price, qty, `provenance = "observed"`, `coverage` |
| `sessions` | start, stop, reason for stopping, bytes, messages, reconnects |

Modelled zones (`trading.liquidations.model_zones`) are **never** written into
`liquidations`. They are recomputed on demand, typed `ModelledZone`, and
reports print them under their own heading, with the model assumptions.

## Correctness

- **Binance book sync:** buffer the diff stream, fetch the REST snapshot, drop events with `u` < `lastUpdateId`, and require `pu` == the previous `u`. Any break triggers a resync and a `sequence_gap` event; bars from a gap are excluded from research.
- **OKX book sync:** verify the CRC32 checksum on every update; on a mismatch, resubscribe and log `checksum_fail`.
- **Clocks:** store the venue timestamp, plus local receive time for latency statistics.

## Operations and safety

- **Start:** only by an explicit owner command, e.g. `python -m jarvis.market_data.recorder --minutes 60 --symbols BTCUSDT,ETHUSDT`.
- **No persistence:** no LaunchAgent, no auto-start, no auto-restart after exit.
- **Time-boxed:** a hard maximum duration (default 60 min, cap 24 h) and a disk cap (default 1 GB), with a stop file as kill switch. The recorder stops itself at whichever comes first.
- **Connections:** one connection per venue. Reconnects are jittered with exponential back-off and limited per hour, through the shared connect budget (AP-33). After the limit, the session ends rather than looping.
- **No keys:** no key is read or accepted. No order endpoints exist in the code, and a guard test forbids trading URLs.
- **Local only:** nothing is forwarded (no Telegram, no cloud). Logs never contain provider response bodies (AP-34).

## Storage estimate

| Recording | Rows per day | Size per day |
|---|---|---|
| Top 20 levels × 2 sides × 2 symbols at 1 s | ≈ 6.9 M | ≈ 350 MB |
| … at 250 ms | ≈ 27.6 M | ≈ 1.4 GB |
| Trades, 2 symbols | 0.5–3 M (market dependent) | ≈ 30–180 MB |
| Liquidations (sampled) | a few thousand | negligible |

A one-hour session at 1 s is therefore ≈ 15–25 MB.

## What the data would be used for

1. **Fills:** a known queue ahead of a resting order (`microsim.Order.queue_ahead`) instead of an estimate, and real depth for liquidity caps.
2. **Spread:** the quoted spread measured directly rather than inferred from trades.
3. **Order flow:** book imbalance and walls (`trading.orderflow`) on real data.
4. **Liquidations:** observed clusters checked against modelled zones. Only the observed side ever counts as evidence.

## Proposed first step (needs approval)

One supervised **60-minute** session, BTC and ETH, Binance only, top 20 at 1 s.
That is about 25 MB, one connection, no restart. The goal is to verify the
sync logic and the size estimate before anything longer.
