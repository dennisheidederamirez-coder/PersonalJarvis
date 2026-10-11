"""``python -m jarvis.live_history`` — sync, status and backfill plan (phase 2: simulated only).

    python -m jarvis.live_history sync --archive PATH --simulated [--dry-run]
    python -m jarvis.live_history status --archive PATH
    python -m jarvis.live_history plan-backfill --archive PATH --since 2026-01-01

``sync`` requires ``--simulated``: the venue client accepts only the simulated
host, and there is no option for real credentials. Each command prints JSON.
Nothing here schedules itself; a run happens only when someone types it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.http_pool import HttpClientPool
from jarvis.live_history.archive import LiveArchive, describe
from jarvis.live_history.simulated_venue import BASE_URL, SIM_CREDENTIALS, SimulatedVenue
from jarvis.live_history.sync import SyncService, plan_backfill
from jarvis.live_history.venue_rest import MIN_INTERVAL_S, VenueReadClient

VENUE = "primary"
ACCOUNT = "main"


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _sync(archive: LiveArchive, *, now: int, dry_run: bool) -> dict[str, Any]:
    venue = SimulatedVenue(now_ms=now)
    client = VenueReadClient(
        BASE_URL,
        SIM_CREDENTIALS,
        pool=HttpClientPool(timeout_s=5.0, transport=venue.transport()),
        # The simulator is in-process; the venue's own ceiling is pacing enough.
        min_interval_s=MIN_INTERVAL_S,
    )
    report = await SyncService(
        client, archive, venue=VENUE, account=ACCOUNT, source="simulated"
    ).run(dry_run=dry_run)
    return {
        "dry_run": report.dry_run,
        "status": report.status.value,
        "requests": report.requests,
        "changes": {e.value: dict(c) for e, c in report.counts.items()},
        "rejected": report.rejected,
        "windows_ok": report.windows_ok,
        "windows_incomplete": len(report.windows_failed),
        "error": report.error_code,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.live_history")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="one sync run against the SIMULATED venue")
    s.add_argument("--archive", type=Path, required=True)
    s.add_argument("--simulated", action="store_true", required=True)
    s.add_argument("--dry-run", action="store_true")
    st = sub.add_parser("status", help="what the archive holds and its last runs")
    st.add_argument("--archive", type=Path, required=True)
    pb = sub.add_parser("plan-backfill", help="missing windows and how to fill them")
    pb.add_argument("--archive", type=Path, required=True)
    pb.add_argument("--since", required=True, help="UTC date, YYYY-MM-DD")
    args = parser.parse_args(argv)

    if args.cmd == "sync":
        now = _now_ms()
        if args.dry_run and not args.archive.exists():
            archive = LiveArchive.in_memory(now_ms=now)  # a dry run never creates the file
        else:
            archive = LiveArchive.open(args.archive, now_ms=now)
        try:
            out: Any = asyncio.run(_sync(archive, now=now, dry_run=args.dry_run))
        finally:
            archive.close()
    else:
        if not args.archive.exists():
            print(json.dumps({"error": "no archive at this path"}))
            return 1
        archive = LiveArchive.open(args.archive, now_ms=_now_ms())
        try:
            if args.cmd == "status":
                out = describe(archive, venue=VENUE, account=ACCOUNT)
            else:
                since = int(
                    datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
                )
                out = [
                    w.__dict__
                    for w in plan_backfill(
                        archive, venue=VENUE, account=ACCOUNT, since_ms=since, now_ms=_now_ms()
                    )
                ]
        finally:
            archive.close()
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
