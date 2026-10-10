"""The journal reader: read-only, consistent, bounded, honest about failures."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from jarvis.trading.journal import SqliteJournal
from jarvis.trading_dashboard.reader import JournalReader, JournalSnapshot, ReadFailure, SourceState


def _journal(tmp: Path) -> SqliteJournal:
    j = SqliteJournal(tmp / "j.sqlite")
    j.commit([("step", 1, "*", {"processed": {}})], {"last_job_run": 1})
    return j


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reads_state_and_rows(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    j.commit([("bar", 5, "BTC-USD", {"stream": "A:BTC-USD", "equity": 1.0})], {"x": {"a": 1}})
    snap = JournalReader(j.path).read()
    assert isinstance(snap, JournalSnapshot)
    assert snap.state["x"] == {"a": 1} and snap.state["last_job_run"] == 1
    assert [r.kind for r in snap.rows] == ["step", "bar"]
    assert snap.rows[1].data["stream"] == "A:BTC-USD"
    assert not snap.truncated and snap.bad_rows == 0


def test_reading_never_changes_the_file(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    before, mtime = _digest(j.path), j.path.stat().st_mtime_ns
    reader = JournalReader(j.path)
    for _ in range(3):
        reader.read()
    assert _digest(j.path) == before and j.path.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in tmp_path.iterdir()) == ["j.sqlite"]  # no -journal / -wal left


def test_the_connection_refuses_writes(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    conn = JournalReader(j.path)._connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO trading_state (key, value) VALUES ('k', '1')")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE t (x)")
    finally:
        conn.close()


def test_a_missing_journal_is_reported_and_not_created(tmp_path: Path) -> None:
    path = tmp_path / "nope" / "j.sqlite"
    result = JournalReader(path).read()
    assert isinstance(result, ReadFailure) and result.state is SourceState.MISSING
    assert not path.exists() and not path.parent.exists()


def test_a_foreign_file_is_invalid(tmp_path: Path) -> None:
    garbage = tmp_path / "g.sqlite"
    garbage.write_bytes(b"this is not a database" * 100)
    result = JournalReader(garbage).read()
    assert isinstance(result, ReadFailure) and result.state is SourceState.INVALID

    other = tmp_path / "other.sqlite"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE something (x)")
    conn.commit()
    conn.close()
    result = JournalReader(other).read()
    assert isinstance(result, ReadFailure) and result.state is SourceState.INVALID


def test_a_held_write_lock_gives_busy_quickly(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    writer = sqlite3.connect(j.path, isolation_level=None)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        sleeps: list[float] = []
        reader = JournalReader(j.path, busy_ms=20, sleep=sleeps.append)
        started = time.monotonic()
        result = reader.read()
        assert isinstance(result, ReadFailure) and result.state is SourceState.BUSY
        assert time.monotonic() - started < 2.0
        assert len(sleeps) == 3
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert isinstance(JournalReader(j.path).read(), JournalSnapshot)


def test_reads_while_the_job_writes_never_block_the_writer(tmp_path: Path) -> None:
    """The writer (busy_timeout 5 s) keeps committing while a reader polls hard;
    every commit lands and every successful read is a whole snapshot."""
    j = _journal(tmp_path)
    errors: list[BaseException] = []
    commits = 200

    def write() -> None:
        try:
            for n in range(commits):
                j.commit(
                    [("bar", n, "BTC-USD", {"stream": "A:BTC-USD", "n": n})],
                    {"counter": n},
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    thread = threading.Thread(target=write)
    thread.start()
    reader = JournalReader(j.path, busy_ms=50)
    while thread.is_alive():
        result = reader.read()
        if isinstance(result, JournalSnapshot):
            bars = [r for r in result.rows if r.kind == "bar"]
            counter = result.state.get("counter")
            # the state and the rows come from the same commit
            assert (counter is None and not bars) or bars[-1].data["n"] == counter
        else:
            assert result.state is SourceState.BUSY
    thread.join()
    assert not errors
    final = JournalReader(j.path).read()
    assert isinstance(final, JournalSnapshot) and final.state["counter"] == commits - 1


def test_snapshots_are_cached_until_the_file_changes(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    reader = JournalReader(j.path)
    first = reader.read()
    assert reader.read() is first
    time.sleep(0.01)
    j.commit([("step", 2, "*", {})], {})
    second = reader.read()
    assert second is not first and isinstance(second, JournalSnapshot)
    assert len(second.rows) == 2


def test_undecodable_rows_are_skipped_and_counted(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    conn = sqlite3.connect(j.path)
    conn.execute(
        "INSERT INTO trading_journal (ts_ms, kind, symbol, data) VALUES (3, 'bar', 'X', '{bad')"
    )
    conn.commit()
    conn.close()
    snap = JournalReader(j.path).read()
    assert isinstance(snap, JournalSnapshot)
    assert snap.bad_rows == 1 and [r.kind for r in snap.rows] == ["step"]


def test_the_row_cap_marks_a_truncated_snapshot(tmp_path: Path) -> None:
    j = _journal(tmp_path)
    j.commit([("bar", n, "X", {"n": n}) for n in range(10)], {})
    snap = JournalReader(j.path, max_rows=4).read()
    assert isinstance(snap, JournalSnapshot)
    assert snap.truncated and [r.data.get("n") for r in snap.rows] == [6, 7, 8, 9]
