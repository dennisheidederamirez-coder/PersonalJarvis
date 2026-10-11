"""The durable LIVE archive: one SQLite file, separate from every paper journal.

What it guarantees:

- **Stable keys, no duplicates.** A record is keyed by ``(venue, account,
  entity, venue id)``. Fetching it again is ``unchanged``; a changed meaning
  (status, fee, funding, close price …) is ``updated`` and leaves a revision;
  a change only in a volatile mark (unrealized P/L) refreshes the stored copy
  without a revision.
- **Traceable sync state.** Every run is a row (who, when, source, how many
  requests, what changed, how it ended); every revision names its run; the
  time windows a run fetched completely are recorded as coverage, so gaps —
  and therefore what a backfill must fetch — are computable.
- **LIVE only.** ``lh_meta.mode`` is ``live`` from creation on, opening a file
  that holds a paper journal (``trading_journal``) is refused, and the paper
  dashboard's reader rejects this file in turn.

Writes happen inside ``batch()``; ``batch(commit=False)`` rolls everything
back, which is how a dry run computes exactly what an apply would change.
The file uses SQLite's default rollback journal and a 5 s busy timeout, the
same discipline as the paper journal, so a read-only dashboard reader can
open it concurrently.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from jarvis.live_history.models import (
    Entity,
    Position,
    Record,
    content_hash,
    from_data,
    to_data,
)

SCHEMA_VERSION: Final = 1
MODE: Final = "live"

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS lh_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lh_records (
    venue TEXT NOT NULL,
    account TEXT NOT NULL,
    entity TEXT NOT NULL,
    record_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    sort_ms INTEGER NOT NULL,
    data TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL,
    first_run_id INTEGER NOT NULL,
    last_run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (venue, account, entity, record_key)
);
CREATE INDEX IF NOT EXISTS lh_records_time ON lh_records (venue, account, entity, sort_ms);
CREATE TABLE IF NOT EXISTS lh_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    account TEXT NOT NULL,
    entity TEXT NOT NULL,
    record_key TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    observed_ms INTEGER NOT NULL,
    old_hash TEXT,
    new_hash TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lh_revisions_key
    ON lh_revisions (venue, account, entity, record_key, observed_ms);
CREATE TABLE IF NOT EXISTS lh_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    account TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    started_ms INTEGER NOT NULL,
    finished_ms INTEGER,
    status TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lh_coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    account TEXT NOT NULL,
    stream TEXT NOT NULL,
    from_ms INTEGER NOT NULL,
    to_ms INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    CHECK (to_ms > from_ms)
);
CREATE INDEX IF NOT EXISTS lh_coverage_stream ON lh_coverage (venue, account, stream, from_ms);
"""

#: Tables of a paper-trading journal; their presence means "wrong file".
_PAPER_TABLES: Final = frozenset({"trading_journal", "trading_state"})


class ArchiveError(RuntimeError):
    """The file is not a LIVE archive this code can use."""


class Outcome(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"  # some windows or records failed; coverage only for the good ones
    FAILED = "failed"


@dataclass(frozen=True)
class SyncRun:
    id: int
    venue: str
    account: str
    kind: str
    source: str
    started_ms: int
    finished_ms: int | None
    status: str
    requests: int
    inserted: int
    updated: int
    unchanged: int
    rejected: int
    error_code: str | None
    detail: str


@dataclass(frozen=True)
class Revision:
    record_key: str
    run_id: int
    observed_ms: int
    old_hash: str | None
    new_hash: str
    record: Record


class LiveArchive:
    def __init__(self, conn: sqlite3.Connection, path: str) -> None:
        self._conn = conn
        self.path = path
        self._depth = 0

    # ------------------------------------------------------------------ opening

    @classmethod
    def open(cls, path: Path, *, now_ms: int) -> LiveArchive:
        """Open or create the archive at ``path``; refuses a paper journal."""
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5, isolation_level=None)
        return cls._init(conn, str(path), now_ms)

    @classmethod
    def in_memory(cls, *, now_ms: int) -> LiveArchive:
        """A throwaway archive (a dry run against no archive yet)."""
        return cls._init(sqlite3.connect(":memory:", isolation_level=None), ":memory:", now_ms)

    @classmethod
    def _init(cls, conn: sqlite3.Connection, path: str, now_ms: int) -> LiveArchive:
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if tables & _PAPER_TABLES:
                raise ArchiveError("this file is a paper-trading journal, not a LIVE archive")
            if tables and "lh_meta" not in tables:
                raise ArchiveError("this file is not a LIVE archive")
            conn.executescript(_SCHEMA)
            meta = dict(conn.execute("SELECT key, value FROM lh_meta").fetchall())
            if not meta:
                conn.executemany(
                    "INSERT INTO lh_meta (key, value) VALUES (?, ?)",
                    [
                        ("mode", MODE),
                        ("schema_version", str(SCHEMA_VERSION)),
                        ("created_ms", str(now_ms)),
                    ],
                )
            elif meta.get("mode") != MODE:
                raise ArchiveError(f"archive mode is {meta.get('mode')!r}, expected {MODE!r}")
            elif int(meta.get("schema_version", "0")) > SCHEMA_VERSION:
                raise ArchiveError("archive was written by a newer version")
        except BaseException:
            conn.close()
            raise
        return cls(conn, path)

    def close(self) -> None:
        self._conn.close()

    def meta(self) -> dict[str, str]:
        return dict(self._conn.execute("SELECT key, value FROM lh_meta").fetchall())

    # ------------------------------------------------------------- transactions

    @contextmanager
    def batch(self, *, commit: bool = True) -> Iterator[None]:
        """One transaction; nested calls join the outer one."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT" if commit else "ROLLBACK")
        finally:
            self._depth = 0

    # --------------------------------------------------------------------- runs

    def begin_run(self, *, venue: str, account: str, kind: str, source: str, now_ms: int) -> int:
        cur = self._conn.execute(
            "INSERT INTO lh_sync_runs (venue, account, kind, source, started_ms, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (venue, account, kind, source, now_ms, RunStatus.RUNNING),
        )
        return int(cur.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        status: RunStatus,
        now_ms: int,
        requests: int,
        counts: dict[Outcome, int],
        rejected: int,
        error_code: str | None = None,
        detail: str = "",
    ) -> None:
        self._conn.execute(
            "UPDATE lh_sync_runs SET finished_ms = ?, status = ?, requests = ?, inserted = ?,"
            " updated = ?, unchanged = ?, rejected = ?, error_code = ?, detail = ? WHERE id = ?",
            (
                now_ms,
                status,
                requests,
                counts.get(Outcome.INSERTED, 0),
                counts.get(Outcome.UPDATED, 0),
                counts.get(Outcome.UNCHANGED, 0),
                rejected,
                error_code,
                detail,
                run_id,
            ),
        )

    def runs(self, *, venue: str, account: str, limit: int = 20) -> list[SyncRun]:
        rows = self._conn.execute(
            "SELECT id, venue, account, kind, source, started_ms, finished_ms, status, requests,"
            " inserted, updated, unchanged, rejected, error_code, detail FROM lh_sync_runs"
            " WHERE venue = ? AND account = ? ORDER BY id DESC LIMIT ?",
            (venue, account, limit),
        ).fetchall()
        return [SyncRun(*r) for r in rows]

    # ------------------------------------------------------------------ records

    def upsert(self, record: Record, *, run_id: int, now_ms: int, source: str) -> Outcome:
        new_hash = content_hash(record)
        data = json.dumps(to_data(record), sort_keys=True, separators=(",", ":"))
        ident = (record.venue, record.account, record.entity.value, record.key())
        row = self._conn.execute(
            "SELECT content_hash, data FROM lh_records"
            " WHERE venue = ? AND account = ? AND entity = ? AND record_key = ?",
            ident,
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO lh_records (venue, account, entity, record_key, symbol, sort_ms,"
                " data, content_hash, first_seen_ms, last_seen_ms, first_run_id, last_run_id,"
                " source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *ident,
                    _symbol(record),
                    record.sort_ms(),
                    data,
                    new_hash,
                    now_ms,
                    now_ms,
                    run_id,
                    run_id,
                    source,
                ),
            )
            self._revision(ident, run_id, now_ms, None, new_hash, data)
            return Outcome.INSERTED
        old_hash, old_data = row
        self._conn.execute(
            "UPDATE lh_records SET symbol = ?, sort_ms = ?, data = ?, content_hash = ?,"
            " last_seen_ms = ?, last_run_id = ?"
            " WHERE venue = ? AND account = ? AND entity = ? AND record_key = ?",
            (_symbol(record), record.sort_ms(), data, new_hash, now_ms, run_id, *ident),
        )
        if old_hash == new_hash:
            return Outcome.UNCHANGED
        self._revision(ident, run_id, now_ms, old_hash, new_hash, data)
        return Outcome.UPDATED

    def _revision(
        self,
        ident: tuple[str, str, str, str],
        run_id: int,
        now_ms: int,
        old_hash: str | None,
        new_hash: str,
        data: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO lh_revisions (venue, account, entity, record_key, run_id,"
            " observed_ms, old_hash, new_hash, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*ident, run_id, now_ms, old_hash, new_hash, data),
        )

    def records(
        self,
        entity: Entity,
        *,
        venue: str,
        account: str,
        since_ms: int | None = None,
        until_ms: int | None = None,
    ) -> list[Record]:
        sql = (
            "SELECT data FROM lh_records WHERE venue = ? AND account = ? AND entity = ?"
            " AND sort_ms >= ? AND sort_ms < ? ORDER BY sort_ms, record_key"
        )
        lo = since_ms if since_ms is not None else -(2**62)
        hi = until_ms if until_ms is not None else 2**62
        rows = self._conn.execute(sql, (venue, account, entity.value, lo, hi)).fetchall()
        return [from_data(entity, json.loads(r[0])) for r in rows]

    def count(self, entity: Entity, *, venue: str, account: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM lh_records WHERE venue = ? AND account = ? AND entity = ?",
            (venue, account, entity.value),
        ).fetchone()
        return int(row[0])

    def revisions(self, entity: Entity, key: str, *, venue: str, account: str) -> list[Revision]:
        rows = self._conn.execute(
            "SELECT record_key, run_id, observed_ms, old_hash, new_hash, data FROM lh_revisions"
            " WHERE venue = ? AND account = ? AND entity = ? AND record_key = ? ORDER BY id",
            (venue, account, entity.value, key),
        ).fetchall()
        return [
            Revision(k, run, ts, old, new, from_data(entity, json.loads(data)))
            for k, run, ts, old, new, data in rows
        ]

    def positions_as_of(self, ts_ms: int, *, venue: str, account: str) -> dict[str, Position]:
        """Each position as the archive knew it at ``ts_ms`` (from the revisions).

        Revisions only record meaningful changes, so the volatile unrealized
        P/L is whatever it was at that revision — fine for the balance check,
        which uses only fee, funding and realized P/L.
        """
        rows = self._conn.execute(
            "SELECT r.record_key, r.data FROM lh_revisions r"
            " JOIN (SELECT record_key, MAX(id) AS id FROM lh_revisions"
            "       WHERE venue = ? AND account = ? AND entity = ? AND observed_ms <= ?"
            "       GROUP BY record_key) last ON r.id = last.id",
            (venue, account, Entity.POSITION.value, ts_ms),
        ).fetchall()
        out: dict[str, Position] = {}
        for key, data in rows:
            rec = from_data(Entity.POSITION, json.loads(data))
            assert isinstance(rec, Position)
            out[key] = rec
        return out

    # ----------------------------------------------------------------- coverage

    def add_coverage(
        self, *, venue: str, account: str, stream: str, from_ms: int, to_ms: int, run_id: int
    ) -> None:
        self._conn.execute(
            "INSERT INTO lh_coverage (venue, account, stream, from_ms, to_ms, run_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (venue, account, stream, from_ms, to_ms, run_id),
        )

    def coverage(self, *, venue: str, account: str, stream: str) -> list[tuple[int, int]]:
        """The fetched windows of one stream, merged into disjoint sorted intervals."""
        rows = self._conn.execute(
            "SELECT from_ms, to_ms FROM lh_coverage WHERE venue = ? AND account = ?"
            " AND stream = ? ORDER BY from_ms, to_ms",
            (venue, account, stream),
        ).fetchall()
        merged: list[tuple[int, int]] = []
        for lo, hi in rows:
            if merged and lo <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        return merged


def gaps(covered: list[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """The parts of ``[lo, hi)`` that ``covered`` (merged, sorted) leaves open."""
    out: list[tuple[int, int]] = []
    cursor = lo
    for a, b in covered:
        if b <= cursor:
            continue
        if a >= hi:
            break
        if a > cursor:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < hi:
        out.append((cursor, hi))
    return out


def _symbol(record: Record) -> str:
    return getattr(record, "symbol", "") or getattr(record, "margin_coin", "")


def describe(archive: LiveArchive, *, venue: str, account: str) -> dict[str, Any]:
    """A small status summary (the CLI's ``status``)."""
    return {
        "mode": archive.meta().get("mode"),
        "counts": {e.value: archive.count(e, venue=venue, account=account) for e in Entity},
        "runs": [r.__dict__ for r in archive.runs(venue=venue, account=account, limit=5)],
    }


__all__ = [
    "MODE",
    "SCHEMA_VERSION",
    "ArchiveError",
    "LiveArchive",
    "Outcome",
    "Revision",
    "RunStatus",
    "SyncRun",
    "describe",
    "gaps",
]
