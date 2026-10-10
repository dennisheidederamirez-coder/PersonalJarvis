"""Paper-trading journals for the dashboard tests, written by the REAL writer.

``run_paper_test`` drives the real ``PaperRunner`` over a synthetic market
(``tests/fakes/fake_market.py``) into a real ``SqliteJournal``, then records
an enabled 14-day job, its runs and daily reports the way ``PaperJob`` does.
Nothing is mocked: the dashboard reads exactly what the job would write.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis.trading.data import BarSeries
from jarvis.trading.journal import SqliteJournal
from jarvis.trading.paper_report import daily_report
from jarvis.trading.paper_runner import PaperRunner, StreamData
from jarvis.trading.paper_spec import DEFAULT_SPEC, INSTRUMENTS, StreamSpec
from jarvis.trading.timeframes import D1, H4, resample
from tests.fakes.fake_market import T0, trending

SEEDS = {"BTC-USD": 2, "ETH-USD": 3, "SOL-USD": 4, "XRP-USD": 5, "BNB-USD": 6}
START = T0 + 200 * D1 + 5 * 60_000  # five minutes after a daily close


def market(hours: int = 24 * 420) -> dict[str, BarSeries]:
    out: dict[str, BarSeries] = {}
    for stream in DEFAULT_SPEC.streams:
        hourly = trending(hours, seed=SEEDS[stream.symbol], instrument=INSTRUMENTS[stream.symbol])
        s = resample(hourly, stream.interval_ms)
        out[stream.id] = dataclasses.replace(s, source=stream.primary_source)
    return out


class Feed:
    """Serves every bar that has started by ``now`` (the runner drops the forming one)."""

    def __init__(self, bars: dict[str, BarSeries]) -> None:
        self.bars = bars
        self.stale: set[str] = set()

    def __call__(self, stream: StreamSpec, now: int) -> StreamData:
        if stream.id in self.stale:
            return StreamData(None, False, "data stale")
        s = self.bars[stream.id]
        return StreamData(s.slice(0, int((s.ts <= now).sum())), True, "ok", None)


@dataclasses.dataclass
class PaperRun:
    path: Path
    journal: SqliteJournal
    runner: PaperRunner
    feed: Feed
    now: list[int]

    def advance(self, steps: int, every: int = H4, *, job_runs: bool = True) -> None:
        for _ in range(steps):
            self.now[0] += every
            report = self.runner.step()
            if job_runs:
                self.journal.commit(
                    [
                        (
                            "job_run",
                            self.now[0],
                            "*",
                            {
                                "processed": report.processed,
                                "skipped": report.skipped,
                                "requests": 3,
                                "notes": [],
                            },
                        )
                    ],
                    {"last_job_run": self.now[0], "request_budget": {self._day(): 3}},
                )
                self._maybe_report()

    def _day(self) -> str:
        return datetime.fromtimestamp(self.now[0] / 1000, UTC).date().isoformat()

    def _maybe_report(self) -> None:
        today = datetime.fromtimestamp(self.now[0] / 1000, UTC).date()
        day = today - timedelta(days=1)
        if (self.journal.load("last_report_day") or "") >= day.isoformat():
            return
        report: dict[str, Any] = daily_report(self.runner, day)
        self.journal.commit(
            [("daily_report", self.now[0], "*", report)], {"last_report_day": day.isoformat()}
        )


def run_paper_test(tmp: Path, *, days: int = 0, warmup_days: int = 120) -> PaperRun:
    """A journal with ``warmup_days`` of trading history, then an enabled
    14-day job started at the current time and ``days`` days of 4h runs."""
    path = tmp / "paper_journal.sqlite"
    journal = SqliteJournal(path)
    feed = Feed(market())
    now = [START]
    runner = PaperRunner(DEFAULT_SPEC, journal, feed, now_ms=lambda: now[0])
    run = PaperRun(path, journal, runner, feed, now)
    runner.step()
    run.advance(warmup_days, every=D1, job_runs=False)
    enable(journal, now[0])
    run.advance(days * 6)
    return run


def enable(journal: SqliteJournal, now_ms: int, days: int = 14) -> dict[str, Any]:
    job = {
        "enabled": True,
        "digest": DEFAULT_SPEC.digest(),
        "by": "owner",
        "from_ms": now_ms,
        "until_ms": now_ms + days * 86_400_000,
        "code": "c0de" * 16,
    }
    journal.commit([("job_enabled", now_ms, "*", job)], {"job": job})
    return job


__all__ = ["START", "Feed", "PaperRun", "enable", "market", "run_paper_test"]
