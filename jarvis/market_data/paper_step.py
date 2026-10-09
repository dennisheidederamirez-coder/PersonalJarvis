"""Manual one-shot paper step: ``python -m jarvis.market_data.paper_step``.

Offline by default — it reads the local cache only. ``--fetch`` first pulls
newly closed bars for the pre-registered streams (a few keyless public
requests); use it only with the owner's approval for a paper run. Nothing is
scheduled, nothing keeps running, nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from jarvis.market_data.adapters import make_adapter
from jarvis.market_data.paper_feed import StoreProvider, update
from jarvis.market_data.store import BarStore
from jarvis.trading.journal import SqliteJournal
from jarvis.trading.paper_runner import PaperRunner
from jarvis.trading.paper_spec import DEFAULT_SPEC


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, type=Path, help="market-data cache (SQLite)")
    ap.add_argument("--journal", required=True, type=Path, help="paper journal (SQLite)")
    ap.add_argument("--expect", default=None, help="pre-registered spec hash (sha256)")
    ap.add_argument("--fetch", action="store_true", help="fetch newly closed bars first")
    ap.add_argument("--status-only", action="store_true", help="print the status, do not step")
    args = ap.parse_args(argv)

    now = int(time.time() * 1000)
    store = BarStore(args.store)
    if args.fetch:
        adapters = {n: make_adapter(n) for n in ("binance", "okx")}
        rep = asyncio.run(update(DEFAULT_SPEC, store, adapters, now))
        print(f"fetched {sum(rep.fetched.values())} bars; errors: {rep.errors or 'none'}")
    runner = PaperRunner(
        DEFAULT_SPEC,
        SqliteJournal(args.journal),
        StoreProvider(store),
        now_ms=lambda: now,
        expected_digest=args.expect,
    )
    if not args.status_only:
        rep2 = runner.step()
        print(f"processed {rep2.processed or 'nothing'}; skipped {rep2.skipped or 'nothing'}")
    print(runner.status_text())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
