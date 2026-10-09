"""The automated paper-trading job — prepared, OFF by default.

``PaperJob.run_once()`` is what a scheduler would call a few minutes after
every 4h close (00:05, 04:05, … UTC). It does nothing unless the owner has
enabled it for exactly the pre-registered spec hash. When enabled it:

1. takes the run lease (one run at a time; a crashed run's lease expires);
2. fetches only newly closed bars, within a per-run and per-day request
   budget (over budget: no fetch, the step runs on cached data and stale
   streams are skipped);
3. runs one ``PaperRunner.step()`` — simulated orders only, and only where a
   validated rule produced a signal;
4. writes the daily report once per UTC day (journal + a Markdown file);
5. releases the lease and journals the run.

Nothing here registers itself with any scheduler, opens a standing
connection, or sends a message.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from jarvis.market_data.adapters import Adapter
from jarvis.market_data.paper_feed import StoreProvider, update
from jarvis.market_data.store import BarStore
from jarvis.trading.journal import SqliteJournal
from jarvis.trading.paper_report import daily_report, to_markdown
from jarvis.trading.paper_runner import PaperRunner
from jarvis.trading.paper_spec import PaperSpec, SpecError

SLOT_HOURS: Final = (0, 4, 8, 12, 16, 20)
SLOT_DELAY_MIN: Final = 5  # minutes after the bar close: the venue has published it
LEASE_TTL_MS: Final = 15 * 60_000
MAX_REQUESTS_PER_RUN: Final = 40
MAX_REQUESTS_PER_DAY: Final = 300


def code_fingerprint() -> str:
    """SHA-256 over the source of the trading and market-data packages."""
    import hashlib

    import jarvis.market_data as md
    import jarvis.trading as tr

    h = hashlib.sha256()
    for pkg in (tr, md):
        root = Path(str(pkg.__file__)).parent
        for path in sorted(root.glob("*.py")):
            h.update(f"{pkg.__name__}/{path.name}\n".encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def next_run(after: datetime) -> datetime:
    """The next scheduled slot strictly after *after* (UTC)."""
    t = after.astimezone(UTC).replace(second=0, microsecond=0)
    for _ in range(24 * 60 + 1):
        t += timedelta(minutes=1)
        if t.hour in SLOT_HOURS and t.minute == SLOT_DELAY_MIN:
            return t
    raise RuntimeError("no slot found")  # pragma: no cover


@dataclass
class RunResult:
    status: str  # disabled / busy / ran
    processed: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    requests: int = 0
    report_path: str | None = None
    notes: list[str] = field(default_factory=list)


class PaperJob:
    def __init__(
        self,
        spec: PaperSpec,
        store: BarStore,
        journal: SqliteJournal,
        adapters: Callable[[], Mapping[str, Adapter]],
        *,
        now_ms: Callable[[], int],
        reports_dir: Path | None = None,
        holder: str | None = None,
    ) -> None:
        self.spec, self.store, self.journal = spec, store, journal
        self.adapters, self.now_ms = adapters, now_ms
        self.reports_dir = reports_dir
        self.holder = holder or f"{socket.gethostname()}:{os.getpid()}"

    # ------------------------------------------------------------ owner switch

    def enable(
        self, approved_digest: str, approved_by: str, *, days: int, code: str | None = None
    ) -> dict[str, Any]:
        """Enable for exactly this spec, for ``days`` days, for exactly this code.

        After ``days`` the job switches itself off. If the trading or
        market-data code changes (``code_fingerprint``), it refuses to run:
        a changed program would make it a different test."""
        if approved_digest != self.spec.digest():
            raise SpecError("approval is for a different specification")
        if not 1 <= days <= 90:
            raise SpecError("a test window is 1-90 days")
        now = self.now_ms()
        job = {
            "enabled": True,
            "digest": approved_digest,
            "by": approved_by,
            "from_ms": now,
            "until_ms": now + days * 86_400_000,
            "code": code or code_fingerprint(),
        }
        self.journal.commit([("job_enabled", now, "*", job)], {"job": job})
        return job

    def disable(self, by: str) -> None:
        self.journal.commit(
            [("job_disabled", self.now_ms(), "*", {"by": by})], {"job": {"enabled": False}}
        )

    def enabled(self) -> bool:
        return self._gate(self.now_ms())[0] == "ok"

    def _gate(self, now: int) -> tuple[str, str]:
        job = self.journal.load("job") or {}
        if not job.get("enabled"):
            return "disabled", "the owner has not enabled the job"
        if job.get("digest") != self.spec.digest():
            return "disabled", "enabled for a different specification"
        if "until_ms" in job and now >= int(job["until_ms"]):
            return "expired", "the approved test window has ended"
        if "code" in job and job["code"] != code_fingerprint():
            return "code_changed", "the trading code differs from the approved version"
        return "ok", ""

    # ------------------------------------------------------------------- run

    def run_once(self, *, fetch: bool = True) -> RunResult:
        now = self.now_ms()
        gate, why = self._gate(now)
        if gate == "expired":
            job = dict(self.journal.load("job") or {})
            job["enabled"] = False
            self.journal.commit(
                [("job_expired", now, "*", {"until_ms": job.get("until_ms")})], {"job": job}
            )
            return RunResult("expired", notes=[why])
        if gate == "code_changed":
            self.journal.commit([("job_refused", now, "*", {"reason": why})], {})
            return RunResult("code_changed", notes=[why])
        if gate != "ok":
            return RunResult("disabled", notes=[why])
        if not self.journal.acquire_lease(self.holder, now, LEASE_TTL_MS):
            return RunResult("busy", notes=["another run holds the lease"])
        result = RunResult("ran")
        try:
            day = datetime.fromtimestamp(now / 1000, UTC).date().isoformat()
            budget = self.journal.load("request_budget") or {}
            used_today = int(budget.get(day, 0))
            if fetch and used_today + MAX_REQUESTS_PER_RUN > MAX_REQUESTS_PER_DAY:
                result.notes.append("daily request budget reached: no fetch this run")
                fetch = False
            if fetch:
                adapters = self.adapters()
                rep = asyncio.run(update(self.spec, self.store, adapters, now))
                result.requests = sum(getattr(a.client, "requests", 0) for a in adapters.values())
                if rep.errors:
                    result.notes.append(f"fetch errors: {rep.errors}")
                if result.requests > MAX_REQUESTS_PER_RUN:
                    result.notes.append(
                        f"run used {result.requests} requests (cap {MAX_REQUESTS_PER_RUN})"
                    )
            runner = PaperRunner(
                self.spec,
                self.journal,
                StoreProvider(self.store),
                now_ms=lambda: now,
                expected_digest=self.spec.digest(),
            )
            step = runner.step()
            result.processed, result.skipped = step.processed, step.skipped
            result.report_path = self._daily_report(runner, now)
            budget = {day: used_today + result.requests}  # keep only today
            self.journal.commit(
                [
                    (
                        "job_run",
                        now,
                        "*",
                        {
                            "processed": result.processed,
                            "skipped": result.skipped,
                            "requests": result.requests,
                            "notes": result.notes,
                        },
                    )
                ],
                {"request_budget": budget, "last_job_run": now},
            )
        finally:
            self.journal.release_lease(self.holder)
        return result

    def _daily_report(self, runner: PaperRunner, now: int) -> str | None:
        """Report the previous UTC day once, after its last bar has closed."""
        today = datetime.fromtimestamp(now / 1000, UTC).date()
        day = today - timedelta(days=1)
        if (self.journal.load("last_report_day") or "") >= day.isoformat():
            return None
        report: dict[str, Any] = daily_report(runner, day)
        path = None
        if self.reports_dir is not None:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            target = self.reports_dir / f"paper-{day.isoformat()}.md"
            target.write_text(to_markdown(report), encoding="utf-8")
            path = str(target)
        self.journal.commit(
            [("daily_report", now, "*", report)], {"last_report_day": day.isoformat()}
        )
        return path


__all__ = [
    "code_fingerprint",
    "launchd_plist",
    "main",
    "LEASE_TTL_MS",
    "MAX_REQUESTS_PER_DAY",
    "MAX_REQUESTS_PER_RUN",
    "SLOT_HOURS",
    "PaperJob",
    "RunResult",
    "next_run",
]


# --------------------------------------------------------------- operations


def launchd_plist(
    python: str,
    workdir: str,
    store: str,
    journal: str,
    reports: str,
    log: str,
    label: str = "local.jarvis.paper-test",
) -> str:
    """A macOS LaunchAgent that calls ``run`` at the six slots (local file,
    user scope). Generated for review — installing it is the activation step.
    launchd runs a slot missed during sleep once on wake; the runner catches
    up every closed bar exactly once either way."""
    from datetime import datetime as _dt

    offset_min = int(_dt.now().astimezone().utcoffset().total_seconds() // 60)  # type: ignore[union-attr]
    slots = []
    for hour in SLOT_HOURS:
        local = (hour * 60 + SLOT_DELAY_MIN + offset_min) % (24 * 60)
        slots.append(
            f"    <dict><key>Hour</key><integer>{local // 60}</integer>"
            f"<key>Minute</key><integer>{local % 60}</integer></dict>"
        )
    args = [
        python,
        "-m",
        "jarvis.market_data.paper_job",
        "--store",
        store,
        "--journal",
        journal,
        "--reports",
        reports,
        "run",
    ]
    arg_xml = "\n".join(f"    <string>{a}</string>" for a in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>WorkingDirectory</key><string>{workdir}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
  </array>
  <key>StartCalendarInterval</key>
  <array>
{chr(10).join(slots)}
  </array>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
"""


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import time

    from jarvis.market_data.adapters import make_adapter
    from jarvis.trading.paper_spec import DEFAULT_SPEC

    ap = argparse.ArgumentParser(prog="python -m jarvis.market_data.paper_job")
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--journal", required=True, type=Path)
    ap.add_argument("--reports", type=Path, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("run")
    en = sub.add_parser("enable")
    en.add_argument("--digest", required=True)
    en.add_argument("--days", type=int, required=True)
    en.add_argument("--by", required=True)
    dis = sub.add_parser("disable")
    dis.add_argument("--by", required=True)
    args = ap.parse_args(argv)

    job = PaperJob(
        DEFAULT_SPEC,
        BarStore(args.store),
        SqliteJournal(args.journal),
        lambda: {n: make_adapter(n) for n in ("binance", "okx")},
        now_ms=lambda: int(time.time() * 1000),
        reports_dir=args.reports,
    )
    if args.cmd == "enable":
        print(json.dumps(job.enable(args.digest, args.by, days=args.days), indent=1))
    elif args.cmd == "disable":
        job.disable(args.by)
        print("disabled")
    elif args.cmd == "run":
        r = job.run_once()
        print(
            json.dumps(
                {
                    "status": r.status,
                    "processed": r.processed,
                    "skipped": r.skipped,
                    "requests": r.requests,
                    "report": r.report_path,
                    "notes": r.notes,
                }
            )
        )
    else:
        state = job.journal.load("job") or {}
        gate, why = job._gate(job.now_ms())
        print(
            json.dumps(
                {
                    "gate": gate,
                    "why": why,
                    "job": state,
                    "code_now": code_fingerprint(),
                    "next_run_utc": next_run(datetime.now(UTC)).isoformat(),
                },
                indent=1,
            )
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
