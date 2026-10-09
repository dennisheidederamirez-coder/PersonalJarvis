"""Every decision, rejection, fill, trade and risk event — in memory or SQLite.

The demo is only worth something if it can be audited: what was decided,
why, what the risk manager said, and what it cost. The SQLite journal also
keeps the risk state (kill switch, seen order ids) across restarts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from jarvis.trading.risk import RiskState


class Journal(Protocol):
    def record(self, kind: str, ts_ms: int, symbol: str, data: dict[str, Any]) -> None: ...


class MemoryJournal:
    def __init__(self) -> None:
        self.entries: list[tuple[str, int, str, dict[str, Any]]] = []

    def record(self, kind: str, ts_ms: int, symbol: str, data: dict[str, Any]) -> None:
        self.entries.append((kind, ts_ms, symbol, dict(data)))

    def kinds(self, kind: str) -> list[dict[str, Any]]:
        return [d for k, _ts, _s, d in self.entries if k == kind]


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS trading_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_ms INTEGER NOT NULL,
        kind TEXT NOT NULL,
        symbol TEXT NOT NULL,
        data TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS trading_journal_kind ON trading_journal (kind, ts_ms)",
    """CREATE TABLE IF NOT EXISTS trading_risk_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        state TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trading_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trading_lease (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        holder TEXT NOT NULL,
        until_ms INTEGER NOT NULL
    )""",
)


class BufferJournal:
    """Collects one step's entries; ``SqliteJournal.commit`` writes them
    together with the state, in one transaction."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, int, str, dict[str, Any]]] = []

    def record(self, kind: str, ts_ms: int, symbol: str, data: dict[str, Any]) -> None:
        self.entries.append((kind, ts_ms, symbol, dict(data)))


class SqliteJournal:
    """Synchronous on purpose (the engine is synchronous); app code calls it
    through ``asyncio.to_thread``. One file per demo account."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """One short connection per call: committed on success, always closed."""
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            with conn:
                yield conn
        finally:
            conn.close()

    def record(self, kind: str, ts_ms: int, symbol: str, data: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trading_journal (ts_ms, kind, symbol, data) VALUES (?, ?, ?, ?)",
                (int(ts_ms), kind, symbol, json.dumps(data, default=str, sort_keys=True)),
            )

    def read(self, kind: str | None = None) -> list[tuple[int, str, str, dict[str, Any]]]:
        with self._conn() as conn:
            if kind:
                cur = conn.execute(
                    "SELECT ts_ms, kind, symbol, data FROM trading_journal"
                    " WHERE kind = ? ORDER BY id",
                    (kind,),
                )
            else:
                cur = conn.execute(
                    "SELECT ts_ms, kind, symbol, data FROM trading_journal ORDER BY id"
                )
            rows = cur.fetchall()
        return [(int(ts), k, s, json.loads(d)) for ts, k, s, d in rows]

    def commit(
        self,
        entries: list[tuple[str, int, str, dict[str, Any]]],
        state: dict[str, Any],
    ) -> None:
        """Entries and state in ONE transaction: after a crash either both are
        there or neither, so a step is never half-applied or doubled."""
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO trading_journal (ts_ms, kind, symbol, data) VALUES (?, ?, ?, ?)",
                [
                    (int(ts), k, s, json.dumps(d, default=str, sort_keys=True))
                    for k, ts, s, d in entries
                ],
            )
            conn.executemany(
                "INSERT INTO trading_state (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v, default=str, sort_keys=True)) for k, v in state.items()],
            )

    def acquire_lease(self, holder: str, now_ms: int, ttl_ms: int) -> bool:
        """One runner at a time, on every OS: a row with an expiry, taken in
        one transaction. An expired lease (a crashed run) can be taken over."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT holder, until_ms FROM trading_lease WHERE id = 1").fetchone()
            if row is not None and row[0] != holder and int(row[1]) > now_ms:
                return False
            conn.execute(
                "INSERT INTO trading_lease (id, holder, until_ms) VALUES (1, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET holder = excluded.holder,"
                " until_ms = excluded.until_ms",
                (holder, now_ms + ttl_ms),
            )
            return True

    def release_lease(self, holder: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM trading_lease WHERE id = 1 AND holder = ?", (holder,))

    def load(self, key: str) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM trading_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_state(self, state: RiskState) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trading_risk_state (id, state) VALUES (1, ?)"
                " ON CONFLICT (id) DO UPDATE SET state = excluded.state",
                (json.dumps(state.to_dict()),),
            )

    def load_state(self) -> RiskState | None:
        with self._conn() as conn:
            row = conn.execute("SELECT state FROM trading_risk_state WHERE id = 1").fetchone()
        return RiskState.from_dict(json.loads(row[0])) if row else None


__all__ = ["BufferJournal", "Journal", "MemoryJournal", "SqliteJournal"]
