"""The venue's web export layout (no ids), offline, from synthetic files only.

The files are written by ``tests.fakes.fake_live_exports`` with the same
header names and cell shapes as the real export, and values from a
``SimulatedVenue``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from jarvis.live_history import __main__ as cli
from jarvis.live_history.archive import LiveArchive, Outcome, RunStatus
from jarvis.live_history.csv_import import (
    ImportKind,
    ImportOptions,
    IssueCode,
    Layout,
    import_csv,
    resolve_columns,
)
from jarvis.live_history.inspect_export import inspect_export
from jarvis.live_history.models import (
    Entity,
    FillTotal,
    Position,
    derived_position_id,
    is_derived,
)
from jarvis.live_history.reconcile import Code, utc_day
from jarvis.live_history.simulated_venue import SimulatedVenue
from jarvis.live_history.sync import Stream, SyncService
from tests.fakes.fake_live_exports import (
    WEB_FILL_HEADERS,
    WEB_POSITION_HEADERS,
    web_fills_csv,
    web_positions_csv,
)
from tests.fakes.fake_live_venue import NOW_MS, FakeTime, make_client

POSITIONS = ImportOptions(ImportKind.POSITIONS, layout=Layout.WEB_EXPORT)
FILLS = ImportOptions(ImportKind.FILLS, layout=Layout.WEB_EXPORT)


def _now() -> int:
    return NOW_MS


def _venue() -> SimulatedVenue:
    return SimulatedVenue(now_ms=NOW_MS, days=60, closed_positions=20)


def _archive(tmp_path: Path) -> LiveArchive:
    return LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS, origin="synthetic")


def _positions(a: LiveArchive) -> dict[str, Position]:
    return {
        p.key(): p
        for p in a.records(Entity.POSITION, venue="primary", account="main")
        if isinstance(p, Position)
    }


def _derived(p: dict[str, object]) -> str:
    return derived_position_id(str(p["symbol"]), str(p["side"]).lower(), int(p["ctime"]))


def _sync(venue: SimulatedVenue, archive: LiveArchive) -> None:
    asyncio.run(
        SyncService(
            make_client(venue, FakeTime(), wall_ms=lambda: NOW_MS),
            archive,
            venue="primary",
            account="main",
            source="simulated",
            now_ms=_now,
        ).run()
    )


# ---------------------------------------------------------------- positions


def test_positions_get_derived_ids_and_split_contract(tmp_path: Path) -> None:
    venue = _venue()
    first = str(venue.history[0]["positionId"])
    path = web_positions_csv(venue, tmp_path / "pos.csv", cross={first})
    a = _archive(tmp_path)
    report = import_csv(a, path, POSITIONS, now_ms=_now)
    assert report.status is RunStatus.OK and report.rejected == 0
    assert report.pnl_basis is not None and report.pnl_basis.value == "gross"
    assert report.pnl_basis_source == "layout"
    stored = _positions(a)
    assert len(stored) == len(venue.history) and all(is_derived(k) for k in stored)
    for p in venue.history:
        got = stored[_derived(p)]
        assert got.symbol == p["symbol"] and got.side == str(p["side"]).lower()
        assert got.qty == Decimal(p["maxQty"])  # "0.005 BTC" → 0.005
        assert got.realized_pnl == Decimal(p["realizedPNL"])  # the gross column
        assert got.fee == Decimal(p["fee"]) and got.funding == Decimal(p["funding"])
        assert got.margin_mode == ("CROSS" if str(p["positionId"]) == first else "ISOLATION")
        assert got.opened_ms == int(p["ctime"]) // 1000 * 1000  # the export has seconds


def test_net_check_names_exactly_the_contradicting_rows(tmp_path: Path) -> None:
    venue = _venue()
    a, b = venue.history[2], venue.history[7]
    offsets = {str(a["positionId"]): Decimal("0.37"), str(b["positionId"]): Decimal("-44.48")}
    path = web_positions_csv(venue, tmp_path / "pos.csv", net_offset=offsets)
    archive = _archive(tmp_path)
    report = import_csv(archive, path, POSITIONS, now_ms=_now)
    assert report.status is RunStatus.OK  # kept: the gross figure is consistent
    assert report.by_code() == {IssueCode.PNL_COMPONENTS_MISMATCH.value: 2}
    checks = {c.record_key: c for c in archive.checks(venue="primary", account="main")}
    assert set(checks) == {_derived(a), _derived(b)}
    for p in (a, b):
        c = checks[_derived(p)]
        components = Decimal(p["realizedPNL"]) - Decimal(p["fee"]) + Decimal(p["funding"])
        assert c.code == Code.EXPORT_PNL_INCONSISTENT.value
        assert c.derived == components
        assert c.stated - c.derived == offsets[str(p["positionId"])]
    # a corrected export clears the checks it no longer fails
    fixed = web_positions_csv(venue, tmp_path / "fixed.csv")
    import_csv(archive, fixed, POSITIONS, now_ms=_now)
    assert archive.checks(venue="primary", account="main") == []


def test_reimporting_the_same_export_changes_nothing(tmp_path: Path) -> None:
    venue = _venue()
    path = web_positions_csv(venue, tmp_path / "pos.csv")
    a = _archive(tmp_path)
    import_csv(a, path, POSITIONS, now_ms=_now)
    again = import_csv(a, path, POSITIONS, now_ms=_now)
    assert again.counts == {Outcome.UNCHANGED: len(venue.history)}
    assert IssueCode.ALREADY_IMPORTED.value in again.by_code()


def test_an_export_after_the_sync_joins_the_apis_positions(tmp_path: Path) -> None:
    venue = _venue()
    archive = _archive(tmp_path)
    _sync(venue, archive)
    api_ids = set(_positions(archive))
    changed = venue.history[4]
    path = web_positions_csv(
        venue, tmp_path / "pos.csv", fee_offset={str(changed["positionId"]): Decimal("0.25")}
    )
    report = import_csv(archive, path, POSITIONS, now_ms=_now)
    # every row found the API's position: no second copy with a derived id
    assert set(_positions(archive)) == api_ids
    assert report.matched_by_identity == len(venue.history)
    assert IssueCode.MATCHED_BY_IDENTITY.value in report.by_code()
    # the API stays the primary source; the one differing row is reported
    assert report.conflicts == 1 and report.status is RunStatus.PARTIAL
    assert _positions(archive)[str(changed["positionId"])].fee == Decimal(changed["fee"])


def test_two_api_positions_with_one_identity_are_not_guessed(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    base = Position(
        venue="primary",
        account="main",
        position_id="P1",
        symbol="BTCUSDT",
        side="long",
        status="closed",
        qty=Decimal("0.01"),
        entry_price=Decimal("60000"),
        close_price=Decimal("60100"),
        fee=Decimal("0.7"),
        funding=Decimal(0),
        realized_pnl=Decimal("1"),
        unrealized_pnl=None,
        liq_qty=None,
        leverage=None,
        margin_mode="ISOLATION",
        opened_ms=NOW_MS - 3_600_000,
        updated_ms=NOW_MS - 60_000,
    )
    with archive.batch():
        run = archive.begin_run(
            venue="primary", account="main", kind="sync", source="simulated", now_ms=NOW_MS
        )
        archive.upsert(base, run_id=run, now_ms=NOW_MS, source="simulated")
        twin_args = {"venue": "primary", "account": "main", "symbol": "BTCUSDT", "side": "long"}
        assert archive.position_twin(**twin_args, opened_ms=base.opened_ms + 400) == "P1"
        archive.upsert(
            dataclasses.replace(base, position_id="P2", opened_ms=base.opened_ms + 300),
            run_id=run,
            now_ms=NOW_MS,
            source="simulated",
        )
        assert archive.position_twin(**twin_args, opened_ms=base.opened_ms) is None


def _rows(tmp_path: Path, name: str, headers: list[str], rows: list[list[str]]) -> Path:
    path = tmp_path / name
    lines = [",".join(headers), *(",".join(r) for r in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_GOOD = [
    "BTCUSDT Short·CROSS",
    "2026-09-01T10:00:00Z",
    "60000",
    "59000",
    "0.01 BTC",
    "590",
    "10",
    "9.2",
    "-0.1",
    "0.7",
    "2026-09-02T10:00:00Z",
]


def test_rows_with_a_wrong_unit_or_an_unreadable_contract_are_rejected(tmp_path: Path) -> None:
    unit = list(_GOOD)
    unit[4] = "0.01 ETH"
    contract = list(_GOOD)
    contract[0] = "BTCUSDT Sideways"
    path = _rows(tmp_path, "pos.csv", WEB_POSITION_HEADERS, [_GOOD, unit, contract])
    a = _archive(tmp_path)
    report = import_csv(a, path, POSITIONS, now_ms=_now)
    assert report.rejected == 2 and report.status is RunStatus.PARTIAL
    codes = {(i.code, i.line, i.field) for i in report.issues}
    assert (IssueCode.UNIT_MISMATCH, 3, "qty") in codes
    assert (IssueCode.BAD_CHOICE, 4, "contract") in codes
    (only,) = _positions(a).values()
    assert only.side == "short" and only.margin_mode == "CROSS" and only.qty == Decimal("0.01")
    assert a.checks(venue="primary", account="main") == []  # 10 − 0.7 − 0.1 = 9.2


def test_the_standard_layout_refuses_the_web_export(tmp_path: Path) -> None:
    with pytest.raises(Exception) as exc:
        resolve_columns(WEB_POSITION_HEADERS, ImportKind.POSITIONS)
    assert getattr(exc.value, "code", None) is IssueCode.MISSING_COLUMN
    assert resolve_columns(WEB_POSITION_HEADERS, ImportKind.POSITIONS, layout=Layout.WEB_EXPORT)
    assert resolve_columns(WEB_FILL_HEADERS, ImportKind.FILLS, layout=Layout.WEB_EXPORT)
    journal = import_csv(
        _archive(tmp_path),
        _rows(tmp_path, "j.csv", WEB_POSITION_HEADERS, [_GOOD]),
        ImportOptions(ImportKind.JOURNAL, layout=Layout.WEB_EXPORT),
        now_ms=_now,
        dry_run=True,
    )
    assert journal.refused is IssueCode.LAYOUT_UNSUPPORTED


# -------------------------------------------------------------------- fills


def _expected_totals(venue: SimulatedVenue) -> dict[tuple[str, str], tuple[int, Decimal, Decimal]]:
    count: dict[tuple[str, str], int] = defaultdict(int)
    fee: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    pnl: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for t in venue.trades:
        key = (utc_day(int(t["ctime"])), str(t["symbol"]))
        count[key] += 1
        fee[key] += Decimal(t["fee"])
        pnl[key] += Decimal(t["realizedPNL"])
    return {k: (count[k], fee[k], pnl[k]) for k in count}


def test_fills_without_ids_are_kept_only_as_daily_totals(tmp_path: Path) -> None:
    venue = _venue()
    path = web_fills_csv(venue, tmp_path / "fills.csv")
    a = _archive(tmp_path)
    report = import_csv(a, path, FILLS, now_ms=_now)
    assert report.status is RunStatus.OK and report.rejected == 0
    assert IssueCode.KEPT_AS_TOTALS.value in report.by_code()
    # never a fill record, so never assigned to a position
    assert a.count(Entity.FILL, venue="primary", account="main") == 0
    totals = {(t.day, t.symbol): t for t in a.fill_totals(venue="primary", account="main")}
    expected = _expected_totals(venue)
    assert set(totals) == set(expected)
    for key, (n, fee, pnl) in expected.items():
        t = totals[key]
        assert (t.fills, t.fee, t.realized_pnl) == (n, fee, pnl)
        assert t.opens + t.closes == t.fills
    days = sorted({d for d, _ in totals})
    edges = {t.day for t in totals.values() if not t.complete}
    assert edges == {days[0], days[-1]}


def test_identical_rows_are_kept_and_named(tmp_path: Path) -> None:
    venue = _venue()
    path = web_fills_csv(venue, tmp_path / "fills.csv", repeat=2)
    a = _archive(tmp_path)
    report = import_csv(a, path, FILLS, now_ms=_now)
    assert report.by_code().get(IssueCode.IDENTICAL_ROWS.value) == 2
    total = sum(t.fills for t in a.fill_totals(venue="primary", account="main"))
    assert total == len(venue.trades) + 2  # kept: they may be real second fills


def test_totals_never_count_as_fill_coverage(tmp_path: Path) -> None:
    venue = _venue()
    path = web_fills_csv(venue, tmp_path / "fills.csv")
    a = _archive(tmp_path)
    opts = ImportOptions(
        ImportKind.FILLS, layout=Layout.WEB_EXPORT, covers=(NOW_MS - 90 * 86_400_000, NOW_MS)
    )
    report = import_csv(a, path, opts, now_ms=_now)
    assert IssueCode.COVERAGE_NOT_RECORDED.value in report.by_code()
    assert a.coverage(venue="primary", account="main", stream=Stream.FILLS.value) == []


def test_a_partial_day_never_replaces_a_complete_one(tmp_path: Path) -> None:
    a = _archive(tmp_path)
    full = FillTotal(
        "primary", "main", "2026-09-01", "BTCUSDT", 4, 2, 2, Decimal("1"), Decimal("3"), True
    )
    part = FillTotal(
        "primary", "main", "2026-09-01", "BTCUSDT", 1, 1, 0, Decimal("0.2"), Decimal(0), False
    )
    with a.batch():
        assert a.upsert_fill_total(part, run_id=1, source="csv:fills:a") is Outcome.INSERTED
        assert a.upsert_fill_total(full, run_id=2, source="csv:fills:b") is Outcome.UPDATED
        assert a.upsert_fill_total(part, run_id=3, source="csv:fills:c") is Outcome.UNCHANGED
    assert a.fill_totals(venue="primary", account="main") == [full]


# ----------------------------------------------------------- inspect and CLI


def test_inspect_points_to_the_layout_and_reads_it(tmp_path: Path) -> None:
    venue = _venue()
    pos = web_positions_csv(venue, tmp_path / "pos.csv")
    fills = web_fills_csv(venue, tmp_path / "fills.csv")
    standard = inspect_export(pos, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    assert standard["web_export_layout_fits"] is True
    assert "use_layout_web_export" in standard["hints"] and "map_columns" not in standard["hints"]

    r = inspect_export(pos, POSITIONS, now_ms=_now)
    assert r["layout"] == "web_export" and "mapping_refused" not in r
    assert r["columns"]["contract"] == {"contract": len(venue.history)}
    assert r["columns"]["qty"] == {"number_with_unit": len(venue.history)}
    assert r["trial_import"]["status"] == "ok" and r["trial_import"]["pnl_basis"] == "gross"

    f = inspect_export(fills, FILLS, now_ms=_now)
    actions = f["columns"]["action"]
    assert sum(actions.values()) == len(venue.trades) and "unknown" not in actions
    assert f["trial_import"]["status"] == "ok"
    assert "kept_as_totals" in f["trial_import"]["issues"]
    # format only: no cell value of the file appears in the report
    text = str(r) + str(f)
    for p in venue.history[:5]:
        assert str(p["entryPrice"]) not in text


def test_cli_import_takes_the_layout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    venue = _venue()
    path = web_positions_csv(venue, tmp_path / "pos.csv")
    code = cli.main(
        [
            "import",
            "--archive",
            str(tmp_path / "live.sqlite"),
            "--file",
            str(path),
            "--kind",
            "positions",
            "--origin",
            "synthetic",
            "--layout",
            "web_export",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and '"layout": "web_export"' in out and '"status": "ok"' in out
    assert not (tmp_path / "live.sqlite").exists()  # a dry run never creates the archive
