"""A read-only view of a paper-trading journal.

The journal is written by the paper job while the dashboard reads it, so the
reader follows three rules:

1. **Read-only by construction.** The file is opened through
   ``file:<path>?mode=ro`` with ``PRAGMA query_only``; SQLite refuses every
   write on that connection, and a missing file is never created.
2. **Short and consistent.** One deferred transaction reads the state rows
   and the journal rows together (one snapshot, never half a step), then the
   connection closes. The writer waits up to 5 s for readers
   (``SqliteJournal`` sets ``busy_timeout = 5000``); a read here takes
   milliseconds.
3. **Bounded waiting.** While the writer commits, the reader waits at most
   ``busy_ms`` per attempt and retries a few times; after that it reports
   ``busy`` instead of blocking a request thread.

Snapshots are cached on the file's (mtime, size): the journal changes about
six times a day, the dashboard polls every minute.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

log = logging.getLogger(__name__)

#: Newest journal rows kept per snapshot. A 14-day paper test writes a few
#: thousand; the cap only protects the app from a runaway file.
MAX_ROWS: Final = 50_000
_RETRIES: Final = 3
_REQUIRED_TABLES: Final = frozenset({"trading_journal", "trading_state"})


class SourceState(StrEnum):
    OK = "ok"
    MISSING = "missing"  # no journal at the configured path
    BUSY = "busy"  # the writer held the lock for every attempt
    INVALID = "invalid"  # a file, but not a paper-trading journal
    ERROR = "error"  # anything else (unreadable, corrupt, …)


@dataclass(frozen=True)
class JournalRow:
    id: int
    ts_ms: int
    kind: str
    symbol: str
    data: dict[str, Any]


@dataclass(frozen=True)
class JournalSnapshot:
    path: str
    state: dict[str, Any]
    rows: tuple[JournalRow, ...]
    risk_state: dict[str, Any] | None
    mtime_ms: int
    size_bytes: int
    read_ms: float
    truncated: bool = False
    #: rows whose JSON could not be decoded (skipped, counted)
    bad_rows: int = 0


@dataclass(frozen=True)
class ReadFailure:
    path: str
    state: SourceState
    detail: str


@dataclass
class _CacheEntry:
    key: tuple[int, int]
    snapshot: JournalSnapshot


@dataclass
class JournalReader:
    """Reads one journal file; safe to share between request threads."""

    path: Path
    busy_ms: int = 250
    max_rows: int = MAX_ROWS
    sleep: Callable[[float], None] = time.sleep
    _cache: _CacheEntry | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def read(self) -> JournalSnapshot | ReadFailure:
        path = str(self.path)
        try:
            st = self.path.stat()
        except FileNotFoundError:  # reported as SourceState.MISSING; the UI explains it
            return ReadFailure(path, SourceState.MISSING, "no journal at this path")
        except OSError as exc:  # reported as SourceState.ERROR with the OS message
            return ReadFailure(path, SourceState.ERROR, f"cannot stat the journal: {exc}")
        key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if self._cache is not None and self._cache.key == key:
                return self._cache.snapshot
        result = self._read_with_retries(st.st_mtime_ns // 1_000_000, st.st_size)
        if isinstance(result, JournalSnapshot):
            with self._lock:
                self._cache = _CacheEntry(key, result)
        return result

    # ----------------------------------------------------------------- internals

    def _read_with_retries(self, mtime_ms: int, size: int) -> JournalSnapshot | ReadFailure:
        path = str(self.path)
        last = ""
        for attempt in range(_RETRIES):
            try:
                return self._read_once(mtime_ms, size)
            except sqlite3.OperationalError as exc:
                text = str(exc)
                if "locked" in text or "busy" in text:
                    last = text
                    self.sleep(0.05 * (attempt + 1))
                    continue
                if "no such table" in text:
                    return ReadFailure(path, SourceState.INVALID, "not a paper-trading journal")
                log.warning("trading journal unreadable: %s", text)
                return ReadFailure(path, SourceState.ERROR, text)
            except sqlite3.DatabaseError as exc:
                log.warning("trading journal unreadable: %s", exc)
                return ReadFailure(path, SourceState.INVALID, str(exc))
        log.info("trading journal stayed locked for %d attempts: %s", _RETRIES, last)
        return ReadFailure(path, SourceState.BUSY, "the paper job is writing; try again shortly")

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self.busy_ms / 1000, isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_ms)}")
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _read_once(self, mtime_ms: int, size: int) -> JournalSnapshot:
        started = time.perf_counter()
        conn = self._connect()
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if not _REQUIRED_TABLES <= tables:
                raise sqlite3.OperationalError("no such table: trading_journal")
            # One read transaction: the state and the rows belong to the same commit.
            conn.execute("BEGIN")
            try:
                state_rows = conn.execute("SELECT key, value FROM trading_state").fetchall()
                total = int(conn.execute("SELECT COUNT(*) FROM trading_journal").fetchone()[0])
                raw = conn.execute(
                    "SELECT id, ts_ms, kind, symbol, data FROM trading_journal"
                    " ORDER BY id DESC LIMIT ?",
                    (self.max_rows,),
                ).fetchall()
                risk = None
                if "trading_risk_state" in tables:
                    row = conn.execute(
                        "SELECT state FROM trading_risk_state WHERE id = 1"
                    ).fetchone()
                    risk = row[0] if row else None
            finally:
                conn.execute("COMMIT")
        finally:
            conn.close()
        bad = 0
        state: dict[str, Any] = {}
        for key, value in state_rows:
            try:
                state[str(key)] = json.loads(value)
            except (TypeError, ValueError):  # counted in bad_rows and logged once below
                bad += 1
        rows: list[JournalRow] = []
        for rid, ts, kind, sym, data in reversed(raw):
            try:
                decoded = json.loads(data)
            except (TypeError, ValueError):  # counted in bad_rows and logged once below
                bad += 1
                continue
            if not isinstance(decoded, dict):
                bad += 1
                continue
            rows.append(JournalRow(int(rid), int(ts), str(kind), str(sym), decoded))
        risk_state = None
        if risk is not None:
            try:
                risk_state = json.loads(risk)
            except (TypeError, ValueError):  # counted in bad_rows and logged once below
                bad += 1
        if bad:
            log.warning("trading journal: %d undecodable rows skipped", bad)
        return JournalSnapshot(
            path=str(self.path),
            state=state,
            rows=tuple(rows),
            risk_state=risk_state,
            mtime_ms=mtime_ms,
            size_bytes=size,
            read_ms=round((time.perf_counter() - started) * 1000, 2),
            truncated=total > len(raw),
            bad_rows=bad,
        )


__all__ = [
    "MAX_ROWS",
    "JournalReader",
    "JournalRow",
    "JournalSnapshot",
    "ReadFailure",
    "SourceState",
]
