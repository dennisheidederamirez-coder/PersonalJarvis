"""Sync runs against the simulated venue: complete, idempotent, traceable, honest about failures."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from decimal import Decimal
from pathlib import Path

import pytest

from jarvis.live_history import __main__ as cli
from jarvis.live_history.archive import LiveArchive, Outcome, RunStatus
from jarvis.live_history.models import Entity, Order, Position
from jarvis.live_history.simulated_venue import DAY_MS, SimulatedVenue
from jarvis.live_history.sync import (
    FIRST_LOOKBACK_MS,
    Stream,
    SyncService,
    api_windows,
    plan_backfill,
)
from tests.fakes.fake_live_venue import NOW_MS, FakeTime, make_client


def _service(
    venue: SimulatedVenue, archive: LiveArchive, now: list[int] | None = None
) -> SyncService:
    clock = now or [NOW_MS]
    return SyncService(
        make_client(venue, FakeTime()),
        archive,
        venue="primary",
        account="main",
        source="simulated",
        now_ms=lambda: clock[0],
    )


def _in_lookback(items: list[dict], key: str = "ctime") -> int:
    return sum(1 for i in items if int(i[key]) >= NOW_MS - FIRST_LOOKBACK_MS)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_first_sync_archives_everything_in_the_look_back(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS)
    report = await _service(venue, archive).run()
    assert report.status is RunStatus.OK
    assert report.error_code is None and report.rejected == 0
    closed = _in_lookback(venue.history)
    assert archive.count(Entity.POSITION, venue="primary", account="main") == closed + len(
        venue.pending
    )
    assert archive.count(Entity.FILL, venue="primary", account="main") == _in_lookback(venue.trades)
    served_orders = venue._history_orders({"limit": "100"})["total"]
    assert archive.count(Entity.ORDER, venue="primary", account="main") == served_orders + len(
        venue.open_orders
    )
    assert archive.count(Entity.BALANCE, venue="primary", account="main") == 1
    run = archive.runs(venue="primary", account="main")[0]
    assert (run.status, run.requests, run.inserted) == (
        "ok",
        report.requests,
        report.total(Outcome.INSERTED),
    )
    for stream in Stream:
        assert archive.coverage(venue="primary", account="main", stream=stream)[-1][1] == NOW_MS
    assert all(m == "GET" for m, _, _ in venue.log)


async def test_second_sync_finds_only_duplicates(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS)
    await _service(venue, archive).run()
    revisions_before = archive._conn.execute("SELECT COUNT(*) FROM lh_revisions").fetchone()[0]
    clock = [NOW_MS + 60_000]
    again = await _service(venue, archive, clock).run()
    assert again.status is RunStatus.OK
    assert again.total(Outcome.UPDATED) == 0
    # the only new row is the new balance observation
    assert again.counts[Entity.BALANCE][Outcome.INSERTED] == 1
    assert again.total(Outcome.INSERTED) == 1
    revisions_after = archive._conn.execute("SELECT COUNT(*) FROM lh_revisions").fetchone()[0]
    assert revisions_after == revisions_before + 1
    assert again.requests < 15  # incremental: one window per stream plus snapshots


async def test_dry_run_reports_the_same_changes_and_writes_nothing(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    path = tmp_path / "live.sqlite"
    archive = LiveArchive.open(path, now_ms=NOW_MS)
    before = _digest(path)
    dry = await _service(venue, archive).run(dry_run=True)
    assert _digest(path) == before
    assert archive.runs(venue="primary", account="main") == []
    applied = await _service(venue, archive).run()
    assert {e: dict(c) for e, c in dry.counts.items()} == {
        e: dict(c) for e, c in applied.counts.items()
    }


async def test_a_position_closed_after_its_window_is_still_completed(tmp_path: Path) -> None:
    """History is filtered by OPEN time; the open position must still arrive closed."""
    venue = SimulatedVenue(now_ms=NOW_MS)
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS)
    await _service(venue, archive).run()
    later = NOW_MS + 5 * DAY_MS
    venue.now_ms = later
    pid = venue.close_open_position(later - DAY_MS, Decimal("62000"))
    report = await _service(venue, archive, [later]).run()
    assert report.status is RunStatus.OK
    pos = {p.key(): p for p in archive.records(Entity.POSITION, venue="primary", account="main")}
    assert isinstance(pos[pid], Position) and pos[pid].status == "closed"
    revs = archive.revisions(Entity.POSITION, pid, venue="primary", account="main")
    assert [r.record.status for r in revs] == ["open", "closed"]  # type: ignore[union-attr]
    orders = archive.records(Entity.ORDER, venue="primary", account="main")
    assert not [o for o in orders if isinstance(o, Order) and o.status == "new"]
    assert any(p.endswith(pid) or params.get("positionId") == pid for _, p, params in venue.log)


async def test_rejected_item_leaves_its_window_open_and_is_retried(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    venue.corrupt_next = True
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS)
    first = await _service(venue, archive).run()
    assert first.status is RunStatus.PARTIAL
    assert first.rejected == 1
    assert [reason for _, reason in first.windows_failed] == ["rejected_items"]
    failed_window = first.windows_failed[0][0]
    covered = archive.coverage(venue="primary", account="main", stream=Stream.POSITIONS)
    assert all(not (lo <= failed_window.from_ms < hi) for lo, hi in covered)
    retry = await _service(venue, archive, [NOW_MS + 1000]).run(
        windows=api_windows(
            plan_backfill(
                archive,
                venue="primary",
                account="main",
                since_ms=NOW_MS - FIRST_LOOKBACK_MS,
                now_ms=NOW_MS,
            )
        ),
        kind="backfill",
    )
    assert retry.status is RunStatus.OK
    assert retry.total(Outcome.UPDATED) == 0  # the good items of that window were already in


async def test_venue_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    venue.fail_next = deque([503] * 10)
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS)
    report = await _service(venue, archive).run()
    assert report.status is RunStatus.FAILED
    assert report.error_code == "unavailable"
    run = archive.runs(venue="primary", account="main")[0]
    assert (run.status, run.error_code) == ("failed", "unavailable")
    assert archive.coverage(venue="primary", account="main", stream=Stream.FILLS) == []


async def test_backfill_plan_separates_api_from_export(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS, days=150)
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS)
    await _service(venue, archive).run()
    since = NOW_MS - 150 * DAY_MS
    plan = plan_backfill(archive, venue="primary", account="main", since_ms=since, now_ms=NOW_MS)
    orders = [w for w in plan if w.stream is Stream.ORDERS]
    assert orders and all(w.route == "export" and w.note == "beyond_retention" for w in orders)
    api = [w for w in plan if w.route == "api"]
    assert {w.stream for w in api} == {Stream.POSITIONS, Stream.FILLS}
    assert all(w.note == "retention_unknown" for w in api)
    report = await _service(venue, archive).run(windows=api_windows(plan), kind="backfill")
    assert report.status is RunStatus.OK
    closed = sum(1 for p in venue.history if int(p["ctime"]) >= since)
    assert archive.count(Entity.POSITION, venue="primary", account="main") == closed + len(
        venue.pending
    )
    assert [r.kind for r in archive.runs(venue="primary", account="main")] == ["backfill", "sync"]
    rest = plan_backfill(archive, venue="primary", account="main", since_ms=since, now_ms=NOW_MS)
    assert [w.route for w in rest] == ["export"] * len(rest)


def test_cli_sync_status_and_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "live.sqlite"
    assert cli.main(["sync", "--archive", str(path), "--simulated", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not path.exists()
    assert cli.main(["status", "--archive", str(path)]) == 1
    capsys.readouterr()
    assert cli.main(["sync", "--archive", str(path), "--simulated"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert cli.main(["status", "--archive", str(path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "live" and status["counts"]["position"] > 0
    assert cli.main(["plan-backfill", "--archive", str(path), "--since", "2020-01-01"]) == 0
    assert json.loads(capsys.readouterr().out)


def test_cli_has_no_live_mode() -> None:
    with pytest.raises(SystemExit):
        cli.main(["sync", "--archive", "x.sqlite"])  # --simulated is mandatory
