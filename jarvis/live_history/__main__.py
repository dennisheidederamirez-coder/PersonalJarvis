"""``python -m jarvis.live_history`` — sync, CSV import, status and backfill plan.

    python -m jarvis.live_history sync --archive PATH --simulated [--dry-run]
    python -m jarvis.live_history import --archive PATH --file CSV --kind positions|fills|journal
        [--tz Europe/Berlin] [--pnl-basis gross|net_of_fees|net] [--map field=Header ...]
        [--decimal comma|point] [--fee-sign auto|cost_positive|cost_negative]
        [--funding-sign received_positive|paid_positive] [--journal NAME]
        [--covers FROM TO] [--dry-run]
    python -m jarvis.live_history status --archive PATH
    python -m jarvis.live_history plan-backfill --archive PATH --since 2026-01-01

``sync`` requires ``--simulated``: the venue client accepts only the simulated
host, and there is no option for real credentials. ``import`` only reads the
given file. Each command prints JSON; a refused import exits with 2.
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
from jarvis.live_history.csv_import import ImportKind, ImportOptions, import_csv
from jarvis.live_history.reconcile import PnlBasis
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


def _day_ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _mapping(pairs: list[str]) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for pair in pairs:
        name, sep, header = pair.partition("=")
        if not sep or not name.strip() or not header.strip():
            return None
        out[name.strip()] = header.strip()
    return out


def _import(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    mapping = _mapping(args.map)
    if mapping is None:
        return 2, {"error": "bad --map, expected field=Header"}
    opts = ImportOptions(
        kind=ImportKind(args.kind),
        venue=VENUE,
        account=ACCOUNT,
        journal=args.journal,
        mapping=mapping,
        tz=args.tz,
        decimal_comma=None if args.decimal is None else args.decimal == "comma",
        fee_sign=args.fee_sign,
        funding_sign=args.funding_sign,
        pnl_basis=PnlBasis(args.pnl_basis) if args.pnl_basis else None,
        covers=(_day_ms(args.covers[0]), _day_ms(args.covers[1])) if args.covers else None,
    )
    if not args.file.is_file():
        return 2, {"error": "no file at this path"}
    now = _now_ms()
    if args.dry_run and not args.archive.exists():
        archive = LiveArchive.in_memory(now_ms=now)  # a dry run never creates the file
    else:
        archive = LiveArchive.open(args.archive, now_ms=now)
    try:
        report = import_csv(archive, args.file, opts, now_ms=_now_ms, dry_run=args.dry_run)
    finally:
        archive.close()
    return (2 if report.refused else 0), report.as_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.live_history")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="one sync run against the SIMULATED venue")
    s.add_argument("--archive", type=Path, required=True)
    s.add_argument("--simulated", action="store_true", required=True)
    s.add_argument("--dry-run", action="store_true")
    im = sub.add_parser("import", help="import a CSV export (venue history or journal)")
    im.add_argument("--archive", type=Path, required=True)
    im.add_argument("--file", type=Path, required=True)
    im.add_argument("--kind", choices=[k.value for k in ImportKind], required=True)
    im.add_argument("--tz", help="IANA zone of times without an offset, e.g. Europe/Berlin")
    im.add_argument("--pnl-basis", choices=[b.value for b in PnlBasis])
    im.add_argument("--map", action="append", default=[], help="field=Header (repeatable)")
    im.add_argument("--decimal", choices=["comma", "point"])
    im.add_argument(
        "--fee-sign", choices=["auto", "cost_positive", "cost_negative"], default="auto"
    )
    im.add_argument(
        "--funding-sign",
        choices=["received_positive", "paid_positive"],
        default="received_positive",
    )
    im.add_argument("--journal", default="control", help="name of the external journal")
    im.add_argument(
        "--covers",
        nargs=2,
        metavar=("FROM", "TO"),
        help="UTC dates the file covers COMPLETELY (recorded as coverage)",
    )
    im.add_argument("--dry-run", action="store_true")
    st = sub.add_parser("status", help="what the archive holds and its last runs")
    st.add_argument("--archive", type=Path, required=True)
    pb = sub.add_parser("plan-backfill", help="missing windows and how to fill them")
    pb.add_argument("--archive", type=Path, required=True)
    pb.add_argument("--since", required=True, help="UTC date, YYYY-MM-DD")
    args = parser.parse_args(argv)

    if args.cmd == "import":
        code, result = _import(args)
        print(json.dumps(result, indent=2, default=str))
        return code
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
                since = _day_ms(args.since)
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
