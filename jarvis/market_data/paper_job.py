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

    def enable(self, approved_digest: str, approved_by: str) -> None:
        if approved_digest != self.spec.digest():
            raise SpecError("approval is for a different specification")
        self.journal.commit(
            [("job_enabled", self.now_ms(), "*", {"by": approved_by, "digest": approved_digest})],
            {"job": {"enabled": True, "digest": approved_digest, "by": approved_by}},
        )

    def disable(self, by: str) -> None:
        self.journal.commit(
            [("job_disabled", self.now_ms(), "*", {"by": by})], {"job": {"enabled": False}}
        )

    def enabled(self) -> bool:
        job = self.journal.load("job") or {}
        return bool(job.get("enabled")) and job.get("digest") == self.spec.digest()

    # ------------------------------------------------------------------- run

    def run_once(self, *, fetch: bool = True) -> RunResult:
        now = self.now_ms()
        if not self.enabled():
            return RunResult("disabled", notes=["the owner has not enabled the job for this spec"])
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
    "LEASE_TTL_MS",
    "MAX_REQUESTS_PER_DAY",
    "MAX_REQUESTS_PER_RUN",
    "SLOT_HOURS",
    "PaperJob",
    "RunResult",
    "next_run",
]
