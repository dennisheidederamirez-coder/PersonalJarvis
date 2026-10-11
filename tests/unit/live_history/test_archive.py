"""The LIVE archive: stable keys, duplicates, revisions, coverage, and the PAPER/LIVE wall."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from jarvis.live_history.archive import ArchiveError, LiveArchive, Outcome, gaps
from jarvis.live_history.models import Entity, Position, content_hash, from_data, to_data
from jarvis.live_history.symbols import canonical, split
from jarvis.trading.journal import SqliteJournal
from jarvis.trading_dashboard.reader import JournalReader, ReadFailure, SourceState

NOW = 1_790_000_000_000


def _position(**kw: object) -> Position:
    base = dict(
        venue="primary",
        account="main",
        position_id="P1",
        symbol="BTCUSDT",
        side="long",
        status="open",
        qty=Decimal("0.010"),
        entry_price=Decimal("60000.00"),
        close_price=None,
        fee=Decimal("0.36"),
        funding=Decimal("-0.02"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("1.5"),
        liq_qty=None,
        leverage=1,
        margin_mode="ISOLATION",
        opened_ms=NOW - 1000,
        updated_ms=NOW - 1000,
    )
    base.update(kw)
    return Position(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw",
    ["BTCUSDT", "BTC/USDT", "btc-usdt", "BTCUSDT.P", "BTCUSDT PERP", "BTC/USDT:USDT", " btc_usdt "],
)
def test_symbol_spellings_are_one_symbol(raw: str) -> None:
    assert canonical(raw) == "BTCUSDT"
    assert split(raw) == ("BTC", "USDT")


def test_unknown_quote_is_not_guessed() -> None:
    assert split("XAUEUR") is None
    assert canonical("XAU/EUR") == "XAUEUR"


def test_records_round_trip_exactly() -> None:
    p = _position()
    back = from_data(Entity.POSITION, to_data(p))
    assert back == p
    assert to_data(p)["qty"] == "0.01"  # trailing zeros never look like a change


def test_cosmetic_decimal_difference_is_not_a_change() -> None:
    assert content_hash(_position(qty=Decimal("0.010"))) == content_hash(
        _position(qty=Decimal("0.01"))
    )


def test_insert_then_duplicate_then_update_with_revisions(tmp_path: Path) -> None:
    a = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW)
    with a.batch():
        run = a.begin_run(venue="primary", account="main", kind="sync", source="test", now_ms=NOW)
        assert a.upsert(_position(), run_id=run, now_ms=NOW, source="test") is Outcome.INSERTED
        assert a.upsert(_position(), run_id=run, now_ms=NOW + 1, source="test") is Outcome.UNCHANGED
        # only the volatile mark moved: stored copy refreshed, no revision
        moved = _position(unrealized_pnl=Decimal("-3"))
        assert a.upsert(moved, run_id=run, now_ms=NOW + 2, source="test") is Outcome.UNCHANGED
        closed = _position(
            status="closed",
            close_price=Decimal("61000"),
            realized_pnl=Decimal("10"),
            updated_ms=NOW,
        )
        assert a.upsert(closed, run_id=run, now_ms=NOW + 3, source="test") is Outcome.UPDATED
    assert a.count(Entity.POSITION, venue="primary", account="main") == 1
    revs = a.revisions(Entity.POSITION, "P1", venue="primary", account="main")
    assert [r.old_hash is None for r in revs] == [True, False]
    assert revs[-1].record == closed
    stored = a.records(Entity.POSITION, venue="primary", account="main")
    assert stored == [closed]


def test_same_id_in_another_account_is_another_record(tmp_path: Path) -> None:
    a = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW)
    with a.batch():
        run = a.begin_run(venue="primary", account="main", kind="sync", source="test", now_ms=NOW)
        a.upsert(_position(), run_id=run, now_ms=NOW, source="test")
        a.upsert(_position(account="sub1"), run_id=run, now_ms=NOW, source="test")
    assert a.count(Entity.POSITION, venue="primary", account="main") == 1
    assert a.count(Entity.POSITION, venue="primary", account="sub1") == 1


def test_rolled_back_batch_leaves_nothing(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    a = LiveArchive.open(path, now_ms=NOW)
    before = path.read_bytes()
    with a.batch(commit=False):
        run = a.begin_run(venue="primary", account="main", kind="sync", source="test", now_ms=NOW)
        a.upsert(_position(), run_id=run, now_ms=NOW, source="test")
        with a.batch():  # a nested batch joins the outer one and is rolled back with it
            a.upsert(_position(position_id="P2"), run_id=run, now_ms=NOW, source="test")
    assert a.count(Entity.POSITION, venue="primary", account="main") == 0
    assert a.runs(venue="primary", account="main") == []
    assert path.read_bytes() == before


def test_live_archive_is_tagged_and_refuses_a_paper_journal(tmp_path: Path) -> None:
    a = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW)
    assert a.meta()["mode"] == "live"
    a.close()
    paper = SqliteJournal(tmp_path / "paper.sqlite")
    paper.commit([("step", 1, "*", {"processed": {}})], {"last_job_run": 1})
    with pytest.raises(ArchiveError, match="paper-trading journal"):
        LiveArchive.open(tmp_path / "paper.sqlite", now_ms=NOW)


def test_paper_dashboard_rejects_a_live_archive(tmp_path: Path) -> None:
    LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW).close()
    result = JournalReader(tmp_path / "live.sqlite").read()
    assert isinstance(result, ReadFailure)
    assert result.state is SourceState.INVALID


def test_foreign_or_newer_files_are_refused(tmp_path: Path) -> None:
    other = tmp_path / "other.sqlite"
    sqlite3.connect(other).execute("CREATE TABLE something (x)").connection.commit()
    with pytest.raises(ArchiveError, match="not a LIVE archive"):
        LiveArchive.open(other, now_ms=NOW)
    newer = tmp_path / "newer.sqlite"
    LiveArchive.open(newer, now_ms=NOW).close()
    with sqlite3.connect(newer) as conn:
        conn.execute("UPDATE lh_meta SET value = '99' WHERE key = 'schema_version'")
    with pytest.raises(ArchiveError, match="newer version"):
        LiveArchive.open(newer, now_ms=NOW)
    wrong_mode = tmp_path / "paper_mode.sqlite"
    LiveArchive.open(wrong_mode, now_ms=NOW).close()
    with sqlite3.connect(wrong_mode) as conn:
        conn.execute("UPDATE lh_meta SET value = 'paper' WHERE key = 'mode'")
    with pytest.raises(ArchiveError, match="mode"):
        LiveArchive.open(wrong_mode, now_ms=NOW)


def test_coverage_merges_and_gaps_are_exact(tmp_path: Path) -> None:
    a = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW)
    with a.batch():
        for lo, hi in [(0, 10), (5, 20), (30, 40)]:
            a.add_coverage(
                venue="primary", account="main", stream="fills", from_ms=lo, to_ms=hi, run_id=1
            )
    covered = a.coverage(venue="primary", account="main", stream="fills")
    assert covered == [(0, 20), (30, 40)]
    assert gaps(covered, 0, 50) == [(20, 30), (40, 50)]
    assert gaps(covered, 5, 15) == []
    assert gaps([], 3, 7) == [(3, 7)]


def test_positions_as_of_reads_history(tmp_path: Path) -> None:
    a = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW)
    opened = _position()
    closed = replace(
        opened, status="closed", realized_pnl=Decimal("5"), close_price=Decimal("61000")
    )
    with a.batch():
        run = a.begin_run(venue="primary", account="main", kind="sync", source="test", now_ms=NOW)
        a.upsert(opened, run_id=run, now_ms=NOW, source="test")
        a.upsert(closed, run_id=run, now_ms=NOW + 100, source="test")
    assert a.positions_as_of(NOW + 50, venue="primary", account="main")["P1"].status == "open"
    assert a.positions_as_of(NOW + 100, venue="primary", account="main")["P1"].status == "closed"
    assert a.positions_as_of(NOW - 1, venue="primary", account="main") == {}
