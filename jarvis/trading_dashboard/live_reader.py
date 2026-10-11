"""A read-only view of the LIVE archive (``jarvis.live_history``).

Same discipline as the paper journal's reader (``reader.py``), on a different
file that is never mixed with it:

1. **Read-only by construction.** ``file:<path>?mode=ro`` with
   ``PRAGMA query_only``; a missing file is reported, never created. The file
   must say ``lh_meta.mode = live``; a paper journal or any other database is
   ``invalid``.
2. **Short and consistent.** One deferred read transaction reads everything
   the dashboard shows — records, runs, coverage, journal entries and the
   positions as they stood at each balance observation — from one commit;
   then the connection closes. A sync or import waits at most 5 s for it.
3. **Bounded waiting.** A few short retries while a writer commits, then
   ``busy``.

Snapshots are cached on the file's (mtime, size); nothing writes the archive
unless a person runs a sync or an import.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from jarvis.live_history.archive import MODE, LiveArchive, SyncRun
from jarvis.live_history.models import Balance, Entity, Fill, Position
from jarvis.live_history.reconcile import JournalEntry
from jarvis.live_history.sync import Stream
from jarvis.trading_dashboard.reader import ReadFailure, SourceState

log = logging.getLogger(__name__)

#: Records per entity kept in a snapshot (newest first); a runaway guard only.
MAX_RECORDS: Final = 100_000
#: Balance observations checked against the trade history (newest).
MAX_BALANCES: Final = 400
#: Runs listed, and import runs whose issues are read.
MAX_RUNS: Final = 200
MAX_IMPORT_RUNS: Final = 20
_RETRIES: Final = 3

DEFAULT_VENUE: Final = "primary"
DEFAULT_ACCOUNT: Final = "main"


@dataclass(frozen=True)
class LiveSnapshot:
    path: str
    mtime_ms: int
    size_bytes: int
    read_ms: float
    venue: str
    account: str
    created_ms: int | None
    positions: tuple[Position, ...]
    fills: tuple[Fill, ...]
    balances: tuple[Balance, ...]
    #: positions as the archive knew them at each balance in ``balances``
    positions_at: tuple[dict[str, Position], ...]
    orders: int
    #: (entity, venue id) → source of the last write (``simulated``, ``csv:…``, …)
    sources: dict[tuple[str, str], str]
    runs: tuple[SyncRun, ...]
    issues: dict[int, list[tuple[int | None, str, str, str | None]]]
    coverage: dict[str, list[tuple[int, int]]]
    journals: dict[str, tuple[JournalEntry, ...]]
    truncated: bool = False


@dataclass
class _CacheEntry:
    key: tuple[int, int]
    snapshot: LiveSnapshot


@dataclass
class LiveArchiveReader:
    """Reads one LIVE archive; safe to share between request threads."""

    path: Path
    venue: str = DEFAULT_VENUE
    account: str = DEFAULT_ACCOUNT
    busy_ms: int = 250
    max_records: int = MAX_RECORDS
    sleep: Callable[[float], None] = time.sleep
    _cache: _CacheEntry | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def read(self) -> LiveSnapshot | ReadFailure:
        path = str(self.path)
        try:
            st = self.path.stat()
        except FileNotFoundError:  # reported as SourceState.MISSING; the UI explains it
            return ReadFailure(path, SourceState.MISSING, "no live archive at this path")
        except OSError as exc:  # reported as SourceState.ERROR with the OS message
            return ReadFailure(path, SourceState.ERROR, f"cannot stat the archive: {exc}")
        key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if self._cache is not None and self._cache.key == key:
                return self._cache.snapshot
        result = self._read_with_retries(st.st_mtime_ns // 1_000_000, st.st_size)
        if isinstance(result, LiveSnapshot):
            with self._lock:
                self._cache = _CacheEntry(key, result)
        return result

    def _read_with_retries(self, mtime_ms: int, size: int) -> LiveSnapshot | ReadFailure:
        path = str(self.path)
        last = ""
        for attempt in range(_RETRIES):
            try:
                return self._read_once(mtime_ms, size)
            except _NotLive as exc:  # reported as SourceState.INVALID
                return ReadFailure(path, SourceState.INVALID, str(exc))
            except sqlite3.OperationalError as exc:
                text = str(exc)
                if "locked" in text or "busy" in text:
                    last = text
                    self.sleep(0.05 * (attempt + 1))
                    continue
                log.warning("live archive unreadable: %s", text)
                return ReadFailure(path, SourceState.ERROR, text)
            except sqlite3.DatabaseError as exc:
                log.warning("live archive unreadable: %s", exc)
                return ReadFailure(path, SourceState.INVALID, str(exc))
        log.info("live archive stayed locked for %d attempts: %s", _RETRIES, last)
        return ReadFailure(path, SourceState.BUSY, "a sync or import is writing; try again shortly")

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self.busy_ms / 1000, isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_ms)}")
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _read_once(self, mtime_ms: int, size: int) -> LiveSnapshot:
        started = time.perf_counter()
        conn = self._connect()
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if "lh_meta" not in tables or "lh_records" not in tables:
                raise _NotLive("not a live archive")
            conn.execute("BEGIN")
            try:
                snap = self._collect(conn, tables, mtime_ms, size, started)
            finally:
                conn.execute("COMMIT")
        finally:
            conn.close()
        return snap

    def _collect(
        self,
        conn: sqlite3.Connection,
        tables: set[str],
        mtime_ms: int,
        size: int,
        started: float,
    ) -> LiveSnapshot:
        # The archive's own query methods, on a connection that cannot write.
        archive = LiveArchive(conn, str(self.path))
        meta = archive.meta()
        if meta.get("mode") != MODE:
            raise _NotLive("not a live archive")
        v, a = self.venue, self.account
        truncated = False

        def records(entity: Entity) -> list[object]:
            nonlocal truncated
            out = archive.records(entity, venue=v, account=a)
            if len(out) > self.max_records:
                truncated = True
                out = out[-self.max_records :]
            return list(out)

        positions = tuple(p for p in records(Entity.POSITION) if isinstance(p, Position))
        fills = tuple(f for f in records(Entity.FILL) if isinstance(f, Fill))
        balances = tuple(b for b in records(Entity.BALANCE) if isinstance(b, Balance))[
            -MAX_BALANCES:
        ]
        positions_at = tuple(archive.positions_as_of(b.ts_ms, venue=v, account=a) for b in balances)
        sources = {
            (str(e), str(k)): str(s)
            for e, k, s in conn.execute(
                "SELECT entity, record_key, source FROM lh_records WHERE venue = ? AND account = ?",
                (v, a),
            )
        }
        runs = tuple(archive.runs(venue=v, account=a, limit=MAX_RUNS))
        issues: dict[int, list[tuple[int | None, str, str, str | None]]] = {}
        if "lh_import_issues" in tables:
            for run in [r for r in runs if r.kind == "import"][:MAX_IMPORT_RUNS]:
                issues[run.id] = archive.issues(run.id)
        coverage = {s.value: archive.coverage(venue=v, account=a, stream=s.value) for s in Stream}
        journals: dict[str, tuple[JournalEntry, ...]] = {}
        if "lh_journal_entries" in tables:
            journals = {j: tuple(archive.journal_entries(j)) for j in archive.journals()}
        created = meta.get("created_ms")
        return LiveSnapshot(
            path=str(self.path),
            mtime_ms=mtime_ms,
            size_bytes=size,
            read_ms=round((time.perf_counter() - started) * 1000, 2),
            venue=v,
            account=a,
            created_ms=int(created) if created and created.isdigit() else None,
            positions=positions,
            fills=fills,
            balances=balances,
            positions_at=positions_at,
            orders=archive.count(Entity.ORDER, venue=v, account=a),
            sources=sources,
            runs=runs,
            issues=issues,
            coverage=coverage,
            journals=journals,
            truncated=truncated,
        )


class _NotLive(Exception):
    pass


__all__ = [
    "DEFAULT_ACCOUNT",
    "DEFAULT_VENUE",
    "MAX_RECORDS",
    "LiveArchiveReader",
    "LiveSnapshot",
]
