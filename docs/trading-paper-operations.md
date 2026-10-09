# Safe continuous paper operation

What has to hold before the paper test runs on a schedule, and how each
failure is handled. Everything here is built and tested offline. The
automated job is **off**.

## The job

`jarvis/market_data/paper_job.py`, `PaperJob.run_once()`:

- **Schedule:** five minutes after every 4h close (00:05, 04:05, …, 20:05
  UTC; `next_run()`). Daily bars close at 00:00, so the 00:05 run handles
  both. Any other time is harmless: only bars closed by then are used.
- **Off by default:** the job runs only if the owner enabled it for exactly
  the pre-registered spec hash (`enable(digest, by)`), and `disable()`
  stops it. Both are journaled.
- **One run:** take the lease → fetch only newly closed bars within the
  request budget → one `PaperRunner.step()` → the previous day's report,
  once → journal the run → release the lease.
- **Not yet wired** to any scheduler, on purpose. The Jarvis task scheduler
  (calendar trigger, a private tool registry like the Ops-core morning job)
  is the intended host. It needs its own approval and app wiring.

## Restarts

- **The atomic step:** every bar's journal entries and the resulting
  account state (positions, cash, pending orders, risk state with seen
  order ids, last processed bar per stream) commit in one SQLite
  transaction.
- **A crash at any point:** the last committed state stays, and the next
  run redoes the unfinished bar. There is never a doubled trade. This is
  tested with a fresh process every day plus crashes before a commit.
- **Lease:** a row with an expiry in the journal database. A second run
  while one is active returns "busy". A crashed run's lease expires after
  15 minutes, and the next run takes over.
- **Missed slots** (machine asleep, app closed): the next run catches up
  every closed bar exactly once, in time order.

## Data failures

- **Incomplete bars** are never used.
- **Stale data** (the newest closed bar is more than 2 bars old) or a
  primary/backup **disagreement** blocks that stream. The reason is
  journaled and shown in the daily report. Other streams continue.
- **Primary down, backup fresh and plausible:** the backup is used and its
  source is journaled. Funding then falls back to the cost model's
  constant (marked "assumed").
- **Unusable data** (out of order, impossible bars) blocks the stream.
- **Fetch errors** are journaled per source. The step still runs on cached
  data, so stale streams are skipped instead of trading on old prices.

## API limits and terms

- The sources are Binance USD-M (primary) and OKX (backup). Both are
  keyless and read-only, and their terms allow personal use. Bybit is not
  used for installs in the EEA.
- **Request budget:** at most 40 requests per run and 300 per UTC day. When
  the budget is reached, there is no fetch; the step runs on the cache.
- **Normal load:** about 3 requests per 4h run, plus about 9 at the daily
  close.
- The polite client keeps spacing far below the published limits. Retries
  are jittered and bounded, `Retry-After` is honoured, and refusals are not
  retried.
- **No standing connection.** Every run is a handful of short HTTP requests
  through the shared pool.

## Kill switch and limits

- **Per account:** a 10 % drawdown stops new entries and closes all
  positions at the next open. Only the owner lifts it, with the exact
  phrase `reset kill switch`. The 2 % daily loss limit pauses entries until
  the next UTC day.
- **The test's own abort rules** (spec section 6) apply in addition: kill
  switch, futility after 20 trades, data problems, any change ends the
  test.
- **The owner's emergency stop:** `disable()` stops the job before its
  next run. A kill switch the owner sets manually stops new entries at once.

## Reasons for every untraded bar

The journal records, for every bar a stream processed:

- the decision; or
- `no_signal` / `hold` with the strategy's reason (for example "no
  breakout: close inside the 20-bar range …", "no SMA crossover …",
  "warming up …"), plus a note when the daily loss limit would refuse
  entries.

Steps without a new closed bar record `waiting` with the next close time.
Data problems record `no_signal` with "data: …". The daily report counts
these reasons per stream.

## Daily report

`jarvis/trading/paper_report.py` covers each candidate:

- equity and cash;
- realized P&L for the day and in total; unrealized P&L;
- drawdown now and the maximum;
- fees for the day and in total; funding;
- trade counts and the profit factor;
- open positions with mark, unrealized P&L and risk at the stop;
- the reasons for untraded bars;
- the data status per stream.

It is stored in the journal and as Markdown. It is **not** sent anywhere;
Telegram or a briefing section needs separate approval, and the briefing
needs PR #499.

## Approvals needed before automated demo operation

1. **Enable the job** for spec hash
   `5fd96b583982b8c22c69eb5c59fd777475fb59ffceced004e4889a072580e106`
   (`PaperJob.enable`).
2. **Wire the job to a scheduler:** the Jarvis task scheduler, at the six
   daily slots. It needs app wiring and its own review.
3. **The data fetch on that schedule:** about 27 keyless, read-only
   requests a day, capped at 300.
4. **Where reports go:** a local folder only (default), or also an app view.
   Telegram stays off unless approved separately.
5. **Who may lift a kill switch** and how the owner is told (a local report
   by default).
