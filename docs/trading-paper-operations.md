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

## 14-day activation plan (prepared, not executed)

**Shared portfolio risk manager: not required for this test.**

- The two candidates run in separate virtual accounts by design. Each has
  the full limits: open risk ≤ 1.5 % (A) and ≤ 0.5 % (B, one market).
- Together that is at most about 2 % of the combined 20 000 virtual capital
  at the stops.
- A BTC overlap between A and B is possible and visible in the report's
  "all accounts together" section.
- The portfolio layer (`docs/trading-portfolio-risk.md`) becomes necessary
  once strategies share one account or capital.

**Isolation.** The test must not change while it runs:

1. **Frozen code:** export the approved commit with `git archive` into its
   own folder (e.g. `~/pj-paper-run/`). That is not a working copy, so
   nobody can switch its branch.
2. **Own Python environment** in that folder, with the versions used in
   testing (numpy 2.1.3, httpx 0.28.1). This is a one-time install from
   PyPI, free.
3. **`enable` runs from that folder**, so the stored code fingerprint is
   the frozen one. Any later change to that folder refuses every run
   ("code changed").
4. **Fresh journal** `~/pj-trading-data/paper_test_2_auto.sqlite`. The
   manual step 1 stays in its own journal as a record, because it ran
   before the reason journaling existed.

**Schedule.**

- A user LaunchAgent (`launchd_plist()`; no admin rights, no app change)
  runs `paper_job run` at 00:05, 04:05, …, 20:05 UTC. The generator writes
  these as local times.
- The Spain clock change on 2026-10-25 shifts the slots by one hour in
  UTC. They still come after each close, so this is harmless.
- `RunAtLoad` is off. The first run is the next slot after activation, and
  it only warms up (the newest closed bar per stream).

**Window.**

- `enable --days 14` sets `until_ms`. The first run after that journals
  `job_expired` and switches itself off; every later run does nothing.
- The LaunchAgent is then removed. That removal is cleanup, not a safety
  requirement.

**Checks.**

- Before activation:
  - full test suite green;
  - spec hash verified;
  - code fingerprint of the frozen folder recorded;
  - `paper_job status` shows `gate: ok`, the window and the next slot.
- Daily: the Markdown report (`~/pj-trading-data/paper-reports/`) with:
  - runs (expected 6);
  - requests;
  - data status;
  - reasons for untraded bars;
  - accounts, positions and costs.
- After 14 days: a final report with every trade, non-trade, data error
  and result.

**Fallbacks.**

| Problem | What happens |
|---|---|
| Mac asleep or off | missed slots are caught up exactly once at the next run |
| Venue down / errors | fetch errors journaled; the step runs on the cache; stale streams skip |
| Primary and backup disagree | that stream makes no decision until they agree again |
| Two runs at once | lease: the second returns "busy" |
| A run crashes | the bar is redone at the next run, never doubled; the lease expires after 15 min |
| Code or spec changed | every run refused and journaled |
| Account drawdown 10 % | kill switch: positions closed, no entries until the owner resets |
| Owner wants to stop | `paper_job disable --by owner` (takes effect at once), then remove the LaunchAgent |

**Activation steps, after approval.**

1. Create the frozen folder and its environment from the approved commit;
   run the tests there.
2. Show the spec hash and the code fingerprint.
3. `paper_job enable --digest <hash> --days 14 --by owner`.
4. Install and load the LaunchAgent (user scope).
5. Report the result: `status` output, next slot, the LaunchAgent loaded.

Steps 4 and 5 are the actual start.
