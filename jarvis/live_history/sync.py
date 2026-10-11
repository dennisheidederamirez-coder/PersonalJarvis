"""One sync run against the venue, and the backfill planner.

A run, in order:

1. records a ``lh_sync_runs`` row (status ``running``);
2. snapshots the account balance, the open positions and the open orders;
3. for every open position or order the archive knew that is no longer
   open, fetches its final state by id — a position opened long before it
   closed would otherwise slip between history windows;
4. fetches the history streams (closed positions, fills, orders) window by
   window, page by page; a window that came back complete is recorded as
   coverage, a window with a failure or a rejected item is not (the next run
   fetches it again — duplicates are recognized, so nothing is counted twice);
5. finishes the run row: ``ok``, ``partial`` or ``failed``, with request and
   change counts and, on failure, only the error kind.

Incremental windows start where coverage ends, minus ``OVERLAP_MS`` so late
venue-side updates are seen. A dry run executes the very same steps inside
one transaction that is rolled back: it reports exactly what an apply would
change and writes nothing.

The venue's look-back is limited — order history 90 days (documented), the
others undocumented — so ``plan_backfill`` splits the missing windows into
what the API can still serve and what only a manual export can fill.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from jarvis.live_history import normalize
from jarvis.live_history.archive import LiveArchive, Outcome, RunStatus, gaps
from jarvis.live_history.models import Entity, Order, Position, Record
from jarvis.live_history.venue_rest import PAGE_LIMIT, ErrorKind, VenueError, VenueReadClient

log = logging.getLogger(__name__)

HOUR_MS: Final = 3_600_000
DAY_MS: Final = 24 * HOUR_MS

WINDOW_MS: Final = 7 * DAY_MS
MIN_WINDOW_MS: Final = HOUR_MS
OVERLAP_MS: Final = DAY_MS
#: How far back the first sync looks when the archive has no coverage yet.
FIRST_LOOKBACK_MS: Final = 90 * DAY_MS
#: Pages per window before the window is split in two (5 000 items).
MAX_PAGES: Final = 50


class Stream(StrEnum):
    POSITIONS = "positions"  # closed positions
    FILLS = "fills"
    ORDERS = "orders"


#: Documented look-back of each history stream; ``None`` = not documented.
RETENTION_MS: Final[dict[Stream, int | None]] = {
    Stream.POSITIONS: None,
    Stream.FILLS: None,
    Stream.ORDERS: 90 * DAY_MS,
}

_LIST_KEY: Final = {
    Stream.POSITIONS: "positionList",
    Stream.FILLS: "tradeList",
    Stream.ORDERS: "orderList",
}


@dataclass(frozen=True)
class Window:
    stream: Stream
    from_ms: int
    to_ms: int


class _Truncated(Exception):
    """A window held more than ``MAX_PAGES`` pages; it is split and retried."""


@dataclass
class SyncReport:
    dry_run: bool
    kind: str
    status: RunStatus = RunStatus.RUNNING
    run_id: int | None = None
    requests: int = 0
    counts: dict[Entity, Counter[Outcome]] = field(
        default_factory=lambda: {e: Counter() for e in Entity}
    )
    rejected: int = 0
    windows_ok: int = 0
    windows_failed: list[tuple[Window, str]] = field(default_factory=list)
    error_code: str | None = None

    def total(self, outcome: Outcome) -> int:
        return sum(c[outcome] for c in self.counts.values())


@dataclass(frozen=True)
class BackfillWindow:
    stream: Stream
    from_ms: int
    to_ms: int
    route: str  # "api" | "export"
    note: str  # "" | "retention_unknown" | "beyond_retention"


class SyncService:
    def __init__(
        self,
        client: VenueReadClient,
        archive: LiveArchive,
        *,
        venue: str,
        account: str,
        source: str,
        margin_coin: str = "USDT",
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.client = client
        self.archive = archive
        self.venue = venue
        self.account = account
        self.source = source
        self.margin_coin = margin_coin
        self._now = now_ms

    # ---------------------------------------------------------------- planning

    def incremental_windows(self, now_ms: int) -> list[Window]:
        out: list[Window] = []
        for stream in Stream:
            covered = self.archive.coverage(venue=self.venue, account=self.account, stream=stream)
            start = covered[-1][1] - OVERLAP_MS if covered else now_ms - FIRST_LOOKBACK_MS
            retention = RETENTION_MS[stream]
            if retention is not None:
                start = max(start, now_ms - retention + HOUR_MS)
            out.extend(_split(stream, start, now_ms))
        return out

    # -------------------------------------------------------------------- run

    async def run(
        self, *, dry_run: bool = False, windows: list[Window] | None = None, kind: str = "sync"
    ) -> SyncReport:
        report = SyncReport(dry_run=dry_run, kind=kind)
        started_requests = self.client.requests
        with self.archive.batch(commit=False) if dry_run else nullcontext():
            now = self._now()
            with self.archive.batch():
                report.run_id = self.archive.begin_run(
                    venue=self.venue,
                    account=self.account,
                    kind=kind,
                    source=self.source,
                    now_ms=now,
                )
            try:
                await self._snapshots(report, now)
                for w in windows if windows is not None else self.incremental_windows(now):
                    await self._window(report, w)
            except VenueError as exc:
                report.error_code = exc.kind.value
                log.warning("live history sync stopped: %s", exc)
            report.requests = self.client.requests - started_requests
            if report.error_code is not None:
                report.status = (
                    RunStatus.PARTIAL
                    if report.total(Outcome.INSERTED) or report.windows_ok
                    else RunStatus.FAILED
                )
            elif report.windows_failed or report.rejected:
                report.status = RunStatus.PARTIAL
            else:
                report.status = RunStatus.OK
            with self.archive.batch():
                self.archive.finish_run(
                    report.run_id,
                    status=report.status,
                    now_ms=self._now(),
                    requests=report.requests,
                    counts={o: report.total(o) for o in Outcome},
                    rejected=report.rejected,
                    error_code=report.error_code,
                    detail=f"{len(report.windows_failed)} window(s) incomplete"
                    if report.windows_failed
                    else "",
                )
        return report

    async def _snapshots(self, report: SyncReport, now: int) -> None:
        assert report.run_id is not None
        accounts = await self.client.account(self.margin_coin)
        positions = await self.client.pending_positions()
        orders = await self._pending_orders()
        with self.archive.batch():
            for item in _as_list(accounts):
                self._store(
                    report,
                    now,
                    lambda i: normalize.balance(
                        i, venue=self.venue, account=self.account, ts_ms=now
                    ),
                    item,
                )
            open_positions = {
                p.key()
                for item in _as_list(positions)
                if (p := self._store(report, now, self._pending_position, item)) is not None
            }
            open_orders = {
                o.key()
                for item in orders
                if (o := self._store(report, now, self._order, item)) is not None
            }
        await self._vanished(report, now, open_positions, open_orders)

    async def _pending_orders(self) -> list[Any]:
        items: list[Any] = []
        for page in range(MAX_PAGES):
            data = await self.client.pending_orders(skip=page * PAGE_LIMIT)
            batch = _as_list((data or {}).get("orderList") if isinstance(data, dict) else data)
            items.extend(batch)
            if len(batch) < PAGE_LIMIT:
                break
        return items

    async def _vanished(
        self, report: SyncReport, now: int, open_positions: set[str], open_orders: set[str]
    ) -> None:
        """Final state of whatever the archive still holds as open but the venue no longer does."""
        known = self.archive.records(Entity.POSITION, venue=self.venue, account=self.account)
        for rec in known:
            if (
                isinstance(rec, Position)
                and rec.status == "open"
                and rec.position_id not in open_positions
            ):
                data = await self.client.history_position(rec.position_id)
                with self.archive.batch():
                    for item in _as_list((data or {}).get("positionList")):
                        self._store(report, now, self._history_position, item)
        known_orders = self.archive.records(Entity.ORDER, venue=self.venue, account=self.account)
        for rec in known_orders:
            if (
                isinstance(rec, Order)
                and rec.status in ("new", "part_filled", "init")
                and rec.order_id not in open_orders
            ):
                data = await self.client.history_order(rec.order_id)
                with self.archive.batch():
                    for item in _as_list((data or {}).get("orderList")):
                        self._store(report, now, self._order, item)

    async def _window(self, report: SyncReport, w: Window) -> None:
        try:
            items = await self._fetch_window(w)
        except _Truncated:
            if w.to_ms - w.from_ms <= MIN_WINDOW_MS:
                report.windows_failed.append((w, "too_many_items"))
                return
            mid = w.from_ms + (w.to_ms - w.from_ms) // 2
            await self._window(report, Window(w.stream, w.from_ms, mid))
            await self._window(report, Window(w.stream, mid, w.to_ms))
            return
        now = self._now()
        parse = {
            Stream.POSITIONS: self._history_position,
            Stream.FILLS: self._fill,
            Stream.ORDERS: self._order,
        }[w.stream]
        rejected_before = report.rejected
        with self.archive.batch():
            for item in items:
                self._store(report, now, parse, item)
            if report.rejected == rejected_before:
                assert report.run_id is not None
                self.archive.add_coverage(
                    venue=self.venue,
                    account=self.account,
                    stream=w.stream,
                    from_ms=w.from_ms,
                    to_ms=w.to_ms,
                    run_id=report.run_id,
                )
                report.windows_ok += 1
            else:
                report.windows_failed.append((w, "rejected_items"))

    async def _fetch_window(self, w: Window) -> list[Any]:
        fetch = {
            Stream.POSITIONS: self.client.history_positions,
            Stream.FILLS: self.client.history_trades,
            Stream.ORDERS: self.client.history_orders,
        }[w.stream]
        items: list[Any] = []
        for page in range(MAX_PAGES):
            data = await fetch(start_ms=w.from_ms, end_ms=w.to_ms, skip=page * PAGE_LIMIT)
            if data is None:
                return items
            if not isinstance(data, dict):
                raise VenueError(ErrorKind.BAD_RESPONSE, status=200)
            batch = _as_list(data.get(_LIST_KEY[w.stream]))
            items.extend(batch)
            total = data.get("total")
            if len(batch) < PAGE_LIMIT or (isinstance(total, int) and len(items) >= total):
                return items
        raise _Truncated

    # ---------------------------------------------------------------- storing

    def _store(
        self,
        report: SyncReport,
        now: int,
        parse: Callable[[Any], Record],
        item: Any,
    ) -> Record | None:
        assert report.run_id is not None
        try:
            if not isinstance(item, dict):
                raise normalize.BadPayload("not an object")
            record = parse(item)
        except normalize.BadPayload as exc:  # counted as rejected; the window stays uncovered
            report.rejected += 1
            log.warning("live history: rejected a venue item (%s)", exc)
            return None
        outcome = self.archive.upsert(record, run_id=report.run_id, now_ms=now, source=self.source)
        report.counts[record.entity][outcome] += 1
        return record

    def _history_position(self, item: dict[str, Any]) -> Record:
        return normalize.history_position(item, venue=self.venue, account=self.account)

    def _pending_position(self, item: dict[str, Any]) -> Record:
        return normalize.pending_position(item, venue=self.venue, account=self.account)

    def _fill(self, item: dict[str, Any]) -> Record:
        return normalize.fill(item, venue=self.venue, account=self.account)

    def _order(self, item: dict[str, Any]) -> Record:
        return normalize.order(item, venue=self.venue, account=self.account)


def plan_backfill(
    archive: LiveArchive, *, venue: str, account: str, since_ms: int, now_ms: int
) -> list[BackfillWindow]:
    """What is missing since ``since_ms``, and how each piece can still be filled."""
    out: list[BackfillWindow] = []
    for stream in Stream:
        covered = archive.coverage(venue=venue, account=account, stream=stream)
        retention = RETENTION_MS[stream]
        floor = now_ms - retention + HOUR_MS if retention is not None else None
        for lo, hi in gaps(covered, since_ms, now_ms):
            if floor is not None and lo < floor:
                out.append(BackfillWindow(stream, lo, min(hi, floor), "export", "beyond_retention"))
                lo = floor
            if lo >= hi:
                continue
            note = "retention_unknown" if retention is None else ""
            out.extend(
                BackfillWindow(w.stream, w.from_ms, w.to_ms, "api", note)
                for w in _split(stream, lo, hi)
            )
    return out


def api_windows(plan: list[BackfillWindow]) -> list[Window]:
    """The part of a backfill plan the API can serve, as sync windows."""
    return [Window(b.stream, b.from_ms, b.to_ms) for b in plan if b.route == "api"]


def _split(stream: Stream, lo: int, hi: int) -> list[Window]:
    out: list[Window] = []
    cursor = lo
    while cursor < hi:
        end = min(hi, cursor + WINDOW_MS)
        out.append(Window(stream, cursor, end))
        cursor = end
    return out


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    # Not the documented list: the whole response is unusable.
    raise VenueError(ErrorKind.BAD_RESPONSE, status=200)


__all__ = [
    "FIRST_LOOKBACK_MS",
    "OVERLAP_MS",
    "RETENTION_MS",
    "WINDOW_MS",
    "BackfillWindow",
    "Stream",
    "SyncReport",
    "SyncService",
    "Window",
    "api_windows",
    "plan_backfill",
]
