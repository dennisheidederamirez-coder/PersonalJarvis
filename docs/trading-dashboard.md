# Trading (paper) dashboard

A read-only view of the automated paper-trading job: the 14-day test, the
virtual accounts, open positions and risk-limit use, every signal and the
reason it was or was not traded, results per strategy and market, data
quality and job operations, and the daily reports.

**It only reads.** No route or button can start, stop, trade or configure
anything; there is no background process and no notification.

## Architecture

```
paper job's SQLite journal ─► jarvis/trading_dashboard ─► /api/trading/* (GET) ─► Settings hub → "Trading (paper)"
  (written by the job)          reader · views · reasons      trading_routes.py        TradingDeskView + 7 tabs
```

| Layer | Where | What it does |
|---|---|---|
| Reader | `jarvis/trading_dashboard/reader.py` | Opens the journal via `file:…?mode=ro` with `PRAGMA query_only`; one short read transaction (state and rows from the same commit); bounded busy wait (3 × 250 ms) then `busy`; snapshot cached on the file's mtime and size |
| Views | `jarvis/trading_dashboard/views.py` | Pure functions from a snapshot, the pre-registered spec and the time to the wire models; a failed read gives empty sections with `source.state` |
| Reasons | `jarvis/trading_dashboard/reasons.py` | Maps the journal's English sentences to stable codes the UI translates; the original text is always shown too |
| Schema | `jarvis/trading_dashboard/schema.py` ↔ `frontend/src/types/trading.ts` | Wire models, parity-tested (AP-4) |
| Routes | `jarvis/ui/web/trading_routes.py` | Seven GET routes; also reachable as `jarvis api trading …` |
| View | `frontend/src/views/TradingDeskView.tsx`, `components/trading/*` | Existing primitives, Radix tabs, recharts, React Query; polls every 60 s only while open |

The package sits outside `jarvis.trading` on purpose: the paper job pins the
source of `jarvis.trading` and `jarvis.market_data` by hash, so a dashboard
change can never trip the job's code lock.

### Concurrency

The job writes in rollback-journal mode with a 5 s busy timeout. The reader
holds a shared lock only for the milliseconds a read takes, so the writer is
never held up meaningfully. If the writer is mid-commit, the reader waits at
most about a second in total and reports `busy`; the next poll retries. A
missing file is reported (`missing`) and never created; a foreign or corrupt
file is `invalid`; undecodable rows are skipped and counted.

### Freshness and data warnings

`source.warnings` carries: `run_overdue` (a scheduled run is more than 30 min
late), `runs_missed`, `data_problem`, `kill_switch`, `job_refused` (code lock),
`spec_mismatch`, `rows_truncated`, `bad_rows`. The UI shows each as a sentence.

### Times

The engine journals bar events under the bar's open time. The dashboard shows
**close** times for bars and dates a decision at its bar's close; executions
keep the open time they filled at. Relative times ("3 hours ago") are measured
from the moment the server read the journal. The UI uses the local time zone;
the job schedules in UTC.

## Language

The section opens in **German** and has a German/English switch in its header
(`components/trading/tradingI18n.ts`, dictionaries in
`i18n/locales/trading/{de,en}.json`, parity-tested). The choice is a per-viewer
convenience in localStorage. The navigation label `nav.trading` exists in all
four app locales.

## Configuration

```toml
[trading_dashboard]
journal_path = ""   # empty: <data_dir>/trading/paper_journal.sqlite
```

Set it only through `config_writer.set_trading_dashboard_journal_path()`
(never a raw write, AP-7). There is deliberately no route or UI control that
writes it: the dashboard itself changes no configuration.

## Tests

- `tests/unit/trading_dashboard/test_reader.py`: read-only by construction, the file is unchanged after reads, writes are refused, missing/foreign/locked files, concurrent writer never blocked, consistent snapshots, cache, bad rows, row cap.
- `tests/unit/trading_dashboard/test_views.py`: against journals written by the real `PaperRunner` (`tests/fakes/fake_paper_journal.py`); accounts, positions and per-strategy results equal `PaperRunner.status()`; attribution of every row to a stream; paging; reason codes; test states; missed and overdue runs; stale data; kill switch; schedule parity with `paper_job`.
- `tests/unit/trading_dashboard/test_schema_parity.py`: Pydantic ↔ TypeScript fields and nullability; every backend code has a German and an English label.
- `tests/unit/trading_dashboard/test_config.py`: the setter writes only its section; empty means the default path.
- `tests/unit/ui/web/test_trading_routes.py`: GET only (405 for everything else), configured and default paths, nothing created on read, full path not echoed, paging and validation.
- `frontend/src/views/TradingDeskView.test.tsx`: German by default, English switch, only GET requests under `/api/trading`, no action buttons, missing-journal notice, warnings, translated reasons, limit meters.

## Integrating into the app (not done yet — needs the owner's go)

1. Wait until the 14-day paper test has ended and been evaluated; the dashboard does not need the test to stop, but integration should not happen while the test's results are still open.
2. Bring the trading packages into the app's code line: open a PR from this branch (or after rebasing it onto current `main`). It contains `jarvis/trading`, `jarvis/market_data` and the dashboard. Review it like any other PR; nothing is merged automatically.
3. Coordinate with any other session working in the app checkout before updating it; update it only through the normal PR / `agent_land.py` flow.
4. Rebuild the frontend (`npm run build` in `jarvis/ui/web/frontend/`); `dist/` is generated and is not committed from this branch.
5. Point the dashboard at the journal with `set_trading_dashboard_journal_path(...)` (one call, done by the agent with the owner's go), or place a copy at `<data_dir>/trading/paper_journal.sqlite`.
6. Open Settings → Activity → Trading (paper) and check the overview against the paper job's own `status` output.

Rollback: remove the `[trading_dashboard]` setting (the section then shows
"no journal found") or revert the PR. The paper job is unaffected either way.
