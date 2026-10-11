# Live trading history (archive, read-only venue client, reconciliation)

A durable local archive of the owner's REAL exchange history, filled through
a read-only, allow-listed venue client, plus checks that compare it with
itself, with an external trading journal and with the account balance.

**Status: phases 1–2, offline only.** The client can reach nothing but a
simulated venue; there are no real credentials, no live sync, no scheduler
job and no UI yet. LIVE data is kept strictly apart from the paper test.

## Sources and roles

| Source | Role |
|---|---|
| Primary exchange (signed futures REST API, v1) | The source of truth for real positions, fills, orders, fees, funding and balances |
| External trading journal | A control source for reconciliation. It is **not independent**: if it imports from the same exchange, it shows transfer errors, not exchange errors |
| Paper-trading journal | Unrelated. Never read or written by this package |

Vendor names stay out of this public repository; the code calls the
exchange the *primary venue* (`venue = "primary"`).

## Package `jarvis/live_history`

| Module | Purpose |
|---|---|
| `models.py` | `Position`, `Fill`, `Order`, `Balance`. Exact `Decimal` money, UTC milliseconds, the venue's id as `key()`, a content hash that ignores volatile marks and cosmetic zeros |
| `symbols.py` | One canonical symbol (`BTCUSDT`) for `BTC/USDT`, `btc-usdt`, `BTCUSDT.P`, `BTC/USDT:USDT` … |
| `archive.py` | The SQLite archive (own file): records, revisions, sync runs, coverage windows |
| `venue_rest.py` | The signed, rate-limited, GET-only client |
| `normalize.py` | Venue payloads (documented shapes) → records; rejects malformed items |
| `sync.py` | One sync run (dry run or apply), incremental windows, the backfill planner |
| `reconcile.py` | Venue-internal checks, journal matching and comparison, balance check |
| `simulated_venue.py` | The simulated venue: documented endpoints over synthetic, deterministic data |
| `__main__.py` | `python -m jarvis.live_history sync --simulated` · `status` · `plan-backfill` |

The package imports neither `jarvis.trading` nor `jarvis.market_data` (the
paper job pins their source by hash) nor any model SDK; a test enforces it.

## The archive

One SQLite file, tagged `lh_meta.mode = live` at creation. It refuses to open
a paper journal, and the paper dashboard's reader rejects it (`invalid`).

| Table | Holds |
|---|---|
| `lh_records` | The current version of every record, keyed by `(venue, account, entity, venue id)` |
| `lh_revisions` | Every meaningful change of a record, with its run and the full record — so a position's state at any time is recoverable |
| `lh_sync_runs` | Each run: kind (`sync`/`backfill`), source, start/end, status (`ok`/`partial`/`failed`), requests, inserted/updated/unchanged/rejected, error kind |
| `lh_coverage` | The time windows a run fetched completely, per stream |

**Duplicates.** Fetching a record again is `unchanged`; only a change in its
meaning (status, fee, funding, close price …) is `updated` and leaves a
revision. A change only in the unrealized P/L refreshes the stored copy
without a revision. `"0.010"` and `"0.01"` are the same value.

**Transactions.** Writes run in short `BEGIN IMMEDIATE` transactions with a
5 s busy timeout in SQLite's default rollback-journal mode — the same
discipline as the paper journal, so a read-only dashboard reader can open
the file concurrently. A dry run executes the same steps inside one
transaction that is rolled back; against a missing file it uses an
in-memory archive and creates nothing.

## The venue client: safety boundaries

1. **Allow-list.** Six GET paths: account, open positions, closed positions,
   fills, order history, open orders. Any other path raises
   `ForbiddenEndpoint` before a request is built. A guard test parses the
   whole package and fails on any `.post/.put/.patch/.delete/.request/.send/.stream`
   call or any write-endpoint path.
2. **Simulated hosts only.** `SIMULATED_HOSTS` (`simulated-venue.invalid`,
   `127.0.0.1`, `localhost`) is the complete list; the real host is not on it.
   Enabling it is a deliberate code change in an owner-approved later phase.
3. **Rate.** At least 0.5 s between requests by default; nothing faster than
   the venue's ceiling (10/s per account) is accepted. Transport errors, 429
   and 5xx are retried at most three times with full-jitter backoff;
   `Retry-After` is honoured (capped at 30 s). 401/403 are not retried.
4. **No leaks.** Errors carry the kind, HTTP status and the venue's numeric
   code only — never the venue's message, a header, the key or a signature.
   `Credentials` masks itself in `repr`.

Signing (venue documentation): `sha256(sha256(nonce + timestamp + key +
sorted "key+value" query + body) + secret)`; a test checks the documented
worked example.

## Sync and backfill

A run snapshots the balance, open positions and open orders; looks up the
final state of every position or order the archive still holds as open but
the venue no longer does (a position opened long before it closed would
otherwise slip between history windows); then fetches the three history
streams in 7-day windows, 100 items per page. A window that would need more
than 50 pages is split. A window with a rejected item or a failure is not
marked covered, so the next run fetches it again.

Incremental runs start at the end of coverage minus 24 h of overlap. The
first run looks back 90 days.

**Limited look-back.** Order history is served for 90 days (cancelled orders:
3 days); the look-back of positions and fills is not documented.
`plan_backfill(since)` lists every uncovered window and how it can be filled:
`api` (still served; `retention_unknown` where undocumented) or `export`
(`beyond_retention` — only the exchange's own CSV export can fill it, a later
import phase). `api_windows(plan)` turns the plan into sync windows; a
backfill is an ordinary run with `kind = "backfill"`.

## Reconciliation

All findings have a stable code (`reconcile.Code`) for later translation:

- **Venue-internal:** a closed position's fee and realized P/L must equal the
  sums of its fills (`fill_fee_mismatch`, `fill_pnl_mismatch`;
  `fills_missing` when none are archived).
- **Journal:** entries are paired by canonical symbol and side (required),
  open/close time (±2 min), size (±0.5 %) and entry price (±0.05 %). Each
  side is used at most once; when the best candidate is not clearly better
  than the next, nothing is guessed (`ambiguous_match`). Unpaired:
  `missing_in_journal`, `missing_at_venue`. Paired: `status_mismatch`,
  `price_mismatch`, `fee_mismatch`, `funding_mismatch`, `pnl_mismatch`.
  The venue's realized P/L **excludes** fees and funding; the journal entry
  states its basis (`gross`, `net_of_fees`, `net`) and the venue value is
  converted before comparing. Amounts within 0.01 USDT (or 0.05 %) are equal.
- **Balance:** the wallet change between two observations must equal the
  change of `realized − fee + funding` over all positions in between;
  a remainder is `unexplained_balance_change` (deposit, transfer, bonus …).

## Tests

`tests/unit/live_history/` — 63 tests, offline, against the simulated venue:
signing, allow-list and GET-only guard, simulated-hosts-only, pacing, retries,
auth and rejection handling without leaks, archive keys/duplicates/revisions,
the PAPER/LIVE wall in both directions, dry run byte-identical, coverage and
gaps, vanished open positions, rejected items and retry, failure recording,
backfill planning and execution, the CLI, and every reconciliation code.

## Open points for later phases (each needs the owner's go)

- **Phase 3:** CSV import for the exchange's position-history export and for
  the external journal (needs an anonymized sample file).
- **Phase 4:** dashboard LIVE mode on the existing trading dashboard
  (separate GET routes, same reader discipline, never mixed with PAPER).
- **Phase 5:** the owner creates a **read-only** key (with IP binding if the
  exchange offers it) and enters it in a hidden prompt; it is stored only in
  the OS keyring. One manual sync, checked against the exchange's own UI.
  Facts to confirm then: the sign convention of `funding`, whether history
  positions are filtered by open or close time, the look-back of fills and
  positions, and the error codes.
- **Phase 6:** a scheduled sync through the existing scheduler (after the
  ops core lands), no notifications unless approved separately.
