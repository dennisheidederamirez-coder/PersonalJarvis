"""The automated job, offline: off unless enabled for the exact spec, one run
at a time, request budget, daily report once, no scheduler of its own."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jarvis.core.http_pool import HttpClientPool
from jarvis.market_data.adapters import ADAPTERS
from jarvis.market_data.client import PoliteClient
from jarvis.market_data.paper_job import (
    LEASE_TTL_MS,
    MAX_REQUESTS_PER_DAY,
    PaperJob,
    next_run,
)
from jarvis.market_data.store import BarStore
from jarvis.trading.data import DAY_MS, HOUR_MS
from jarvis.trading.journal import SqliteJournal
from jarvis.trading.paper_spec import DEFAULT_SPEC, SpecError
from tests.fakes.fake_exchanges import FakeExchanges

T0 = 1_767_225_600_000
NOW0 = T0 + 600 * DAY_MS + 5 * 60_000  # 00:05 UTC


async def _sleep(_s: float) -> None:
    return None


class Setup:
    def __init__(self, tmp: Path) -> None:
        self.now = [NOW0]
        self.fake = FakeExchanges(self.now[0])
        self.store = BarStore(tmp / "md.sqlite")
        self.journal = SqliteJournal(tmp / "paper.sqlite")
        self.made = 0
        self.job = PaperJob(
            DEFAULT_SPEC,
            self.store,
            self.journal,
            self.adapters,
            now_ms=lambda: self.now[0],
            reports_dir=tmp / "reports",
            holder="test",
        )

    def adapters(self) -> dict:  # type: ignore[type-arg]
        self.made += 1
        self.fake.now_ms = self.now[0]
        return {
            n: ADAPTERS[n](
                PoliteClient(
                    n,
                    min_interval_s=0,
                    pool=HttpClientPool(transport=self.fake.transport()),
                    sleep=_sleep,
                    rng=random.Random(1),  # noqa: S311 - test jitter
                ),
                now_ms=lambda: self.now[0],
            )
            for n in ("binance", "okx")
        }


def test_slots_are_five_minutes_after_each_4h_close() -> None:
    t = datetime(2026, 10, 9, 20, 53, tzinfo=UTC)
    assert next_run(t) == datetime(2026, 10, 10, 0, 5, tzinfo=UTC)
    assert next_run(datetime(2026, 10, 10, 0, 5, tzinfo=UTC)).hour == 4


def test_the_job_is_off_until_enabled_for_this_exact_spec(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    assert s.job.run_once().status == "disabled" and s.made == 0 and s.fake.calls == []
    with pytest.raises(SpecError):
        s.job.enable("0" * 64, "owner", days=14)
    s.job.enable(DEFAULT_SPEC.digest(), "owner", days=14)
    assert s.job.enabled()
    s.job.disable("owner")
    assert s.job.run_once().status == "disabled" and s.made == 0


def test_runs_fetch_step_and_report_once_per_day(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    s.job.enable(DEFAULT_SPEC.digest(), "owner", days=14)
    first = s.job.run_once()
    assert first.status == "ran" and first.requests > 0 and len(first.processed) == 6
    assert first.report_path and Path(first.report_path).exists()
    s.now[0] += 4 * HOUR_MS
    second = s.job.run_once()
    assert second.report_path is None  # the day was already reported
    assert second.processed.get("B:BTC-USD") == 1 and "A:BTC-USD" not in second.processed
    assert second.requests <= first.requests
    kinds = {k for _ts, k, _s, _d in s.journal.read()}
    assert {"job_enabled", "job_run", "daily_report", "waiting"} <= kinds


def test_one_run_at_a_time_and_a_crashed_lease_expires(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    s.job.enable(DEFAULT_SPEC.digest(), "owner", days=14)
    assert s.journal.acquire_lease("other-host:1", s.now[0], LEASE_TTL_MS)
    assert s.job.run_once().status == "busy" and s.made == 0
    s.now[0] += LEASE_TTL_MS + 1  # the other run crashed and never released
    assert s.job.run_once().status == "ran"


def test_the_daily_request_budget_stops_fetching(tmp_path: Path) -> None:
    s = Setup(tmp_path)
    s.job.enable(DEFAULT_SPEC.digest(), "owner", days=14)
    day = datetime.fromtimestamp(s.now[0] / 1000, UTC).date().isoformat()
    s.journal.commit([], {"request_budget": {day: MAX_REQUESTS_PER_DAY}})
    result = s.job.run_once()
    assert result.status == "ran" and result.requests == 0 and s.made == 0
    assert "budget" in result.notes[0]
    assert all(
        "stale" in r or "no data" in r or "unavailable" in r for r in result.skipped.values()
    )  # nothing cached: no decision
