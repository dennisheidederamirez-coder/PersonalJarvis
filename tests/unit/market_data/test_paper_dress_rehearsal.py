"""A 14-day dress rehearsal of the automated paper test, offline: six runs a
day against a fake of the venues, with a missed slot, a parallel run, a venue
outage, a code change and the automatic end after 14 days."""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

from jarvis.core.http_pool import HttpClientPool
from jarvis.market_data.adapters import ADAPTERS
from jarvis.market_data.client import PoliteClient
from jarvis.market_data.paper_job import LEASE_TTL_MS, PaperJob, launchd_plist
from jarvis.market_data.store import BarStore
from jarvis.trading.data import DAY_MS, HOUR_MS
from jarvis.trading.journal import SqliteJournal
from jarvis.trading.paper_spec import DEFAULT_SPEC
from tests.fakes.fake_exchanges import FakeExchanges

T0 = 1_767_225_600_000
START = T0 + 600 * DAY_MS + 5 * 60_000  # 00:05 UTC
SLOT = 4 * HOUR_MS


async def _sleep(_s: float) -> None:
    return None


def test_fourteen_days_end_to_end(tmp_path: Path) -> None:
    now = [START]
    fake = FakeExchanges(now[0])

    def adapters() -> dict:  # type: ignore[type-arg]
        fake.now_ms = now[0]
        return {
            n: ADAPTERS[n](
                PoliteClient(
                    n,
                    min_interval_s=0,
                    pool=HttpClientPool(transport=fake.transport()),
                    sleep=_sleep,
                    rng=random.Random(1),  # noqa: S311 - test jitter
                ),
                now_ms=lambda: now[0],
            )
            for n in ("binance", "okx")
        }

    journal = SqliteJournal(tmp_path / "paper.sqlite")
    job = PaperJob(
        DEFAULT_SPEC,
        BarStore(tmp_path / "md.sqlite"),
        journal,
        adapters,
        now_ms=lambda: now[0],
        reports_dir=tmp_path / "reports",
        holder="launchd",
    )
    job.enable(DEFAULT_SPEC.digest(), "owner", days=14)

    statuses: Counter[str] = Counter()
    for k in range(16 * 6):  # 16 days of slots: the window ends after 14
        if k == 3 * 6 + 2:  # day 3, 08:05: the Mac was asleep — slot missed
            now[0] += SLOT
            continue
        if k == 5 * 6 + 1:  # day 5: another run still holds the lease
            journal.acquire_lease("other:1", now[0], LEASE_TTL_MS)
        if k == 7 * 6 + 3:  # day 7: the venues answer 500 for a while
            fake.fail_next = [500] * 30
        statuses[job.run_once().status] += 1
        now[0] += SLOT

    assert statuses["busy"] == 1
    assert statuses["ran"] == 14 * 6 - 1 - 1  # minus the missed and the busy slot
    assert statuses["expired"] == 1  # the first call after the window switches it off
    assert statuses["disabled"] == 2 * 6 - 1  # every later call does nothing
    rows = journal.read()
    bars = Counter((d["stream"], ts) for ts, k, _s, d in rows if k == "bar")
    assert bars and max(bars.values()) == 1  # no bar twice, despite gaps and outages
    after_end = [ts for ts, k, _s, _d in rows if k == "bar" and ts >= START + 14 * DAY_MS]
    assert after_end == []  # nothing processed after the window
    assert len({d["day"] for *_x, d in journal.read("daily_report")}) >= 14
    assert len(list((tmp_path / "reports").glob("paper-*.md"))) >= 14
    runs = journal.read("job_run")
    assert any("fetch errors" in " ".join(d["notes"]) for *_x, d in runs)  # outage recorded
    reasons = Counter(d["reason"].split(":")[0] for *_x, d in journal.read("no_signal"))
    assert reasons  # every untraded bar has a reason
    assert not job.enabled() and job.run_once().status == "disabled"


def test_a_code_change_stops_the_test(tmp_path: Path) -> None:
    now = [START]
    journal = SqliteJournal(tmp_path / "paper.sqlite")
    job = PaperJob(
        DEFAULT_SPEC, BarStore(tmp_path / "md.sqlite"), journal, lambda: {}, now_ms=lambda: now[0]
    )
    job.enable(DEFAULT_SPEC.digest(), "owner", days=14, code="a-different-program")
    result = job.run_once()
    assert result.status == "code_changed" and journal.read("job_refused")
    assert journal.read("bar") == []


def test_the_launch_agent_is_generated_for_review_not_installed(tmp_path: Path) -> None:
    # generating the plist must not install anything; an agent the owner
    # installed on purpose may already exist, so compare before and after
    agents = Path.home() / "Library" / "LaunchAgents"
    before = {p: p.stat().st_mtime_ns for p in agents.glob("local.jarvis.paper-test*.plist")}
    plist = launchd_plist("/usr/bin/python3", str(tmp_path), "s.sqlite", "j.sqlite", "r", "log")
    assert plist.count("<key>Hour</key>") == 6 and "<false/>" in plist  # no run at load
    assert "jarvis.market_data.paper_job" in plist and "run" in plist
    after = {p: p.stat().st_mtime_ns for p in agents.glob("local.jarvis.paper-test*.plist")}
    assert after == before
