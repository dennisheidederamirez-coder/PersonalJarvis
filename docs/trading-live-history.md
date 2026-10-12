# Live trading history (archive, read-only venue client, reconciliation)

A durable local archive of the owner's REAL exchange history, filled through
a read-only, allow-listed venue client, plus checks that compare it with
itself, with an external trading journal and with the account balance.

**Status: phases 1–4, offline only.** The client can reach nothing but a
simulated venue; there are no real credentials, no live sync and no
scheduler job. CSV exports can be imported (phase 3) and the trading
dashboard has a separate, read-only LIVE view (phase 4, see
`docs/trading-dashboard.md`). All tests and samples use synthetic data. LIVE
data is kept strictly apart from the paper test.

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
| `csv_import.py` | Import of the venue's position/trade exports and an external journal's export |
| `inspect_export.py` | A real export's format (headers, counts, shares, issue codes) without its contents |
| `__main__.py` | `python -m jarvis.live_history sync --simulated` · `import` · `status` · `plan-backfill` |

The package imports neither `jarvis.trading` nor `jarvis.market_data` (the
paper job pins their source by hash) nor any model SDK; a test enforces it.

## The archive

One SQLite file, tagged `lh_meta.mode = live` at creation. It refuses to open
a paper journal, and the paper dashboard's reader rejects it (`invalid`).

**Real and synthetic data never share a file.** `lh_meta.origin` is fixed at
creation: `real` (the owner's account, through the API or its own exports)
or `synthetic` (the simulated venue and generated sample files). Opening an
archive for the other origin is refused; an archive without the label is
refused for writing and shown with a warning. The simulated venue can only
fill a `synthetic` archive (`SyncService` checks it), `sync --simulated`
always opens one, and `import` needs `--origin real|synthetic`. The
dashboard labels a synthetic archive as demo data everywhere (title,
subtitle, banner) and never calls it the real account.

| Table | Holds |
|---|---|
| `lh_records` | The current version of every record, keyed by `(venue, account, entity, venue id)` |
| `lh_revisions` | Every meaningful change of a record, with its run and the full record — so a position's state at any time is recoverable |
| `lh_sync_runs` | Each run: kind (`sync`/`backfill`), source, start/end, status (`ok`/`partial`/`failed`), requests, inserted/updated/unchanged/rejected, error kind |
| `lh_coverage` | The time windows a run fetched (or an import declared) completely, per stream |
| `lh_import_issues` | Per import run: line, code, severity and field of every row it could not use — never a cell's content |
| `lh_journal_entries` | Entries of an external journal, keyed per journal; never mixed with the venue's records |

`lh_records.source` names the source of the last write (`simulated`, later
the real API, or `csv:<kind>:<digest>`).

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

## CSV import (phase 3)

```
python -m jarvis.live_history import --archive PATH --file CSV --kind positions|fills|journal \
    [--tz Europe/Berlin] [--pnl-basis gross|net_of_fees|net] [--map field=Header ...] \
    [--decimal comma|point] [--fee-sign auto|cost_positive|cost_negative] \
    [--funding-sign received_positive|paid_positive] [--journal NAME] [--covers FROM TO] \
    [--layout standard|web_export] [--dry-run]
```

The real export formats of the exchange and of the external journal are
**not known yet**: the importer finds columns by broad header aliases, and
`--map` assigns any header to a field. The synthetic samples
(`tests/fakes/fake_live_exports.py`) cover an API-named style and a
spreadsheet style (semicolons, decimal commas, local times, negative fees,
net P/L). An anonymized real sample is needed before the first real import.

What the importer checks:

| Topic | Rule |
|---|---|
| Duplicates | Keyed by the venue's id (required for venue exports; a journal without ids gets a stable digest). Same id twice in a file: identical → skipped (`duplicate_in_file`), different → both rejected (`conflicting_duplicate`). Re-importing a file changes nothing (`already_imported`) |
| API is primary | A row never overwrites a record the API delivered; a differing row is reported (`conflicts_with_api`). Fields a file leaves empty (liquidation size, leverage, reduce-only …) keep the stored value; a time in whole seconds does not overwrite the same second's milliseconds |
| Times | Epoch s/ms or ISO 8601 with offset; zoneless times need `--tz` (else the file is refused, `timezone_required`). Non-existent local times rejected, doubled ones flagged; before 2017, in the future, or closed before opened → rejected |
| Numbers | Exact decimals; decimal mark from the delimiter (`;` → comma) or `--decimal`; thousands separators only in strict groups, so `12,5` in a point file is rejected rather than read as 125 |
| Fees | Stored as a positive cost; all-negative fees are flipped (`fee_sign_flipped`), mixed signs are flagged; a fee in another currency is rejected |
| Funding | Signed, negative = paid; `--funding-sign paid_positive` for the opposite convention; a missing funding column is flagged |
| P/L definitions | Converted to the venue's realized P/L (excluding fees and funding) from the file's basis — declared, or inferred from prices, fees and funding when ≥ 80 % of the distinguishable rows fit one basis (`pnl_basis_inferred`); a declared basis the rows contradict is flagged (`pnl_basis_doubtful`); otherwise refused (`pnl_basis_required`). Net P/L without funding is refused for positions. Fills carry no prices to infer from: their basis must be declared |
| Missing data | Required values missing → the row is rejected with line, code and field |
| Safety | Read-only; ≤ 20 MB, ≤ 200 000 rows, ≤ 64 columns, ≤ 256 characters per cell; text fields accept a narrow character set (no spreadsheet formulas); UTF-8, falling back to Windows-1252 with a warning |

### The web export layout (`--layout web_export`)

The exchange's own web interface exports positions and fills in a shape the
standard layout cannot read: **no ids at all**, symbol, side and margin mode
in one column (`BTCUSDT Long·ISOLATION`), sizes with their unit
(`0.5 BTC`), ISO times in UTC to the second, and the P/L twice. The format
was established with `inspect` on the owner's files (format only); the
synthetic samples (`web_positions_csv`, `web_fills_csv`) reproduce its
header names and cell shapes with simulated values.

| | Positions | Fills |
|---|---|---|
| Columns | `futures` (symbol · side · margin mode), `opening time`, `average entry price`, `average closing price`, `closed amount` (size + unit), `closed value`, `position pnl` (gross), `realized pnl` (net), `funding fees`, `position fee`, `closed time` | `date`, `side` (Open/Close Long/Short), `coin`, `futures`, `transaction amount`, `average price`, `realized p/l` (gross), `netprofits` (gross − fee), `fee` |
| Id | Derived: `x:` + digest of symbol, side and opening **second** (`models.derived_position_id`) — a venue holds at most one position per symbol and side opening in the same second; two rows with one identity are a duplicate (identical) or both rejected (different) | None, and none can be derived: two fills in the same second look alike |
| Stored as | `Position` records; the gross P/L as `realized_pnl` (the venue's definition) — except where the row contradicts itself (below) | **Only daily totals** per UTC day and symbol (`FillTotal`: fills, opens, closes, fee, realized P/L); never `Fill` records, so never assigned to a position. The file's first and last day are marked incomplete; a complete day replaces a stored one, a partial one never replaces a complete one |
| Self-check | Stated net = gross − fee + funding, exactly. Where a row fails, its **stated net is authoritative** (on the owner's real files it agrees with the fill history, the gross column does not): the stored P/L becomes net + fee − funding, and `lh_record_checks` keeps the stated net, the net the gross implies and the original gross (`pnl_components_mismatch`). A later export that passes clears the check and restores the gross | Stated net = gross − fee; failures are reported per line |
| Unit | The size's unit must be the symbol's base asset (`unit_mismatch` otherwise) | Same |
| Repeats | — | Identical rows are kept and named per line (`identical_rows`): without an id a repeat may be a second fill |
| With the API | A row whose identity matches exactly one position the API delivered is stored under the API's id (`matched_by_identity`); the API stays primary and a differing row is `conflicts_with_api`. Imported **before** a sync, the export's copy stays under its derived id and the dashboard pairs the two copies (see Reconciliation) | `--covers` is not recorded as fill coverage (`coverage_not_recorded`): totals are not fills, and the backfill must still fetch them |

`inspect --layout web_export` reports the format the same way (the
contract column as parsed or not, sizes with unit, the four actions
counted), and the standard `inspect` says when the web layout fits
(`use_layout_web_export`).

Every import is a run (`kind = import`); a refused file leaves a `failed`
run with the reason. `--covers FROM TO` records coverage for the stream only
when the import was clean, so the backfill plan stops listing that span.
`--dry-run` reports the same result and writes nothing.

## Checking the real export formats (before the first real import)

The exchange's and the external journal's real export formats are not known
yet. `inspect` answers what the importer needs to know without revealing
the contents:

```
python -m jarvis.live_history inspect --file EXPORT.csv --kind positions|fills|journal \
    [--tz Europe/Berlin] [--pnl-basis …] [--map field=Header ...] [--decimal comma|point] \
    [--layout standard|web_export]
```

It prints JSON with: size, a 12-digit checksum prefix, encoding, delimiter,
decimal mark, the header names, the column mapping (or why it failed), the
unmapped headers, per mapped column only counts (empty / number / text,
time format class such as `epoch_ms` or `no_zone`, and for fee, funding and
P/L the number of negative, zero and positive values), and a trial import
into a discarded in-memory archive: status, how many rows it would insert
or reject, the P/L definition it recognised, issue codes with line numbers,
and hints (`map_columns`, `declare_timezone`, `declare_pnl_basis`,
`check_decimal_mark`, `restrict_permissions`). No value, id, symbol or
timestamp appears; nothing is written; a header that looks unusual is shown
as `<withheld>`.

Safe procedure for the owner:

1. Download the exports from the exchange (position history, trade history)
   and, if it offers one, from the journal. If a file is `.xlsx`, save it as
   CSV in the spreadsheet program.
2. Keep them in a folder **outside** the repository and readable only by
   you (`chmod 600 <file>`). `inspect` and a real `import` refuse a file
   inside a git working tree.
3. Run `inspect` once per file and share only its JSON output. It contains
   no account data; check it before sharing all the same.
4. From that output the column mapping, time zone, decimal mark and P/L
   definition are fixed, and the aliases are extended where needed — with
   synthetic samples in the tests, never the real files.
5. The first real `import --origin real` goes into a new archive outside the
   repository, as a `--dry-run` first.

Screenshots and logs are never made from real data: the preview harness
uses the synthetic demo archive only, and the importer stores issue codes,
line numbers and field names — never a cell.

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
  A position archived later (a CSV backfill) but closed before the earlier
  observation is not counted as explaining the change.
- **Exports without ids:** `export_pnl_inconsistent` — an export's stated
  net ≠ its own gross − fee + funding (per position: the stated net that is
  used, and the net the original gross implies; the original gross is shown
  next to the adjusted P/L in the Positions tab). `fills_missing` is not
  reported for positions without an exchange id: such an export cannot
  carry fills;
  `export_differs_from_api` — the API's copy of a position against the
  export's copy (same symbol, side and opening second, paired only when
  exactly one API position fits), per differing field. The dashboard shows
  each such position **once**, as the API's copy; the export's copy is
  used only for this comparison, so totals and the balance check never
  count it twice.
- **Fill totals:** `fill_totals_mismatch` — the daily totals of a fill
  export without ids against the archived fills on days their coverage
  spans completely (fee and realized P/L), and otherwise per run of
  consecutive days against the realized P/L of the positions closed in it
  (fees are not compared there: a position's fee includes an opening that
  may lie outside the run). These findings name a symbol and days, never a
  position.

In the dashboard every finding is traceable: a stable id; the position, the
journal entry, the summed fills or the two balance observations it is
about, with symbol, side and open/close times; what was compared (fee,
funding, P/L …); the exchange's value and where its record came from (API
or CSV); the compared value and its source (sum of fills, journal, trade
history); the difference; for P/L the definition both sides were aligned
to; and the likely cause in words (German and English).

## Tests

`tests/unit/live_history/test_inspect_export.py` — no cell content in the
report, nothing written, files inside a git tree refused, unknown columns
reported, `--origin` mandatory, real imports refused inside the repository.

`tests/unit/live_history/test_web_export.py` — the web layout: derived ids,
contract and unit parsing, the net self-check per position (and its
clearing), re-import, joining the API's positions, no guessing between two
API positions, rejected rows, fills kept only as totals (exact sums,
incomplete edge days, repeats kept, no fill coverage), `inspect` and the
CLI. `tests/unit/trading_dashboard/test_live_dashboard.py` covers the
demo with web exports: each position once, every export and totals
finding with its values and sources, no totals finding on a position, the
balance check unchanged.

`tests/unit/live_history/test_csv_import.py` — number and time parsing,
column aliases and mapping, export vs API equality, the API-is-primary rule,
localized exports, duplicates, rejected rows without content leaks, fee and
funding signs, all P/L bases, file limits, encoding, coverage, the journal
import with every reconciliation code, and the CLI.

`tests/unit/live_history/` (phases 1–2) — 63 tests, offline, against the simulated venue:
signing, allow-list and GET-only guard, simulated-hosts-only, pacing, retries,
auth and rejection handling without leaks, archive keys/duplicates/revisions,
the PAPER/LIVE wall in both directions, dry run byte-identical, coverage and
gaps, vanished open positions, rejected items and retry, failure recording,
backfill planning and execution, the CLI, and every reconciliation code.

## Open points for later phases (each needs the owner's go)

- **Before a real import:** `inspect --layout web_export` on the owner's
  exchange exports (format only) to confirm they run without rejected rows;
  then a `--dry-run` into a new real archive outside the repository. The
  external journal's export format is still unknown.
- **Phase 5:** the owner creates a **read-only** key (with IP binding if the
  exchange offers it) and enters it in a hidden prompt; it is stored only in
  the OS keyring. One manual sync, checked against the exchange's own UI.
  Facts to confirm then: the sign convention of `funding`, whether history
  positions are filtered by open or close time, the look-back of fills and
  positions, and the error codes.
- **Phase 6:** a scheduled sync through the existing scheduler (after the
  ops core lands), no notifications unless approved separately.
