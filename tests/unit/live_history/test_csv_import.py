"""CSV import: venue exports and an external journal, offline, from synthetic files only."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from jarvis.live_history import __main__ as cli
from jarvis.live_history.archive import LiveArchive, Outcome, RunStatus
from jarvis.live_history.csv_import import (
    MAX_FILE_BYTES,
    ImportKind,
    ImportOptions,
    IssueCode,
    import_csv,
    infer_pnl_basis,
    parse_decimal,
    parse_time,
    resolve_columns,
)
from jarvis.live_history.models import Entity, Fill, Position
from jarvis.live_history.reconcile import Code, PnlBasis, reconcile_journal
from jarvis.live_history.simulated_venue import DAY_MS, SimulatedVenue
from jarvis.live_history.sync import FIRST_LOOKBACK_MS, Stream, SyncService, plan_backfill
from tests.fakes.fake_live_exports import (
    JournalChanges,
    journal_csv,
    venue_fills_csv,
    venue_positions_csv,
)
from tests.fakes.fake_live_venue import NOW_MS, FakeTime, make_client

_BERLIN = "Europe/Berlin"


def _now() -> int:
    return NOW_MS


def _archive(tmp_path: Path) -> LiveArchive:
    return LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS)


def _positions(a: LiveArchive) -> dict[str, Position]:
    return {
        p.key(): p
        for p in a.records(Entity.POSITION, venue="primary", account="main")
        if isinstance(p, Position)
    }


def _write(tmp_path: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


_POS_HEADER = (
    "positionId,symbol,side,maxQty,entryPrice,closePrice,fee,funding,realizedPNL,ctime,mtime"
)
_T0 = NOW_MS - 30 * DAY_MS


def _pos_row(pid: str = "P1", **over: object) -> str:
    v: dict[str, object] = {
        "positionId": pid,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "maxQty": "0.01",
        "entryPrice": "60000",
        "closePrice": "61000",
        "fee": "0.73",
        "funding": "-0.2",
        "realizedPNL": "10",
        "ctime": _T0,
        "mtime": _T0 + 3_600_000,
    }
    v.update(over)
    return ",".join(str(v[k]) for k in _POS_HEADER.split(","))


def _codes(report: object) -> list[str]:
    return [i.code.value for i in report.issues]  # type: ignore[attr-defined]


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize(
    ("raw", "comma", "expected"),
    [
        ("1234.5", False, "1234.5"),
        ("1,234.50", False, "1234.50"),
        ("1.234,50", True, "1234.50"),
        ("0,193", True, "0.193"),
        ("-0.2 USDT", False, "-0.2"),
        ("−0.2", False, "-0.2"),
        ("(3.10)", False, "-3.10"),
        ("1E-5", False, "0.00001"),
        ("12 345,6", True, "12345.6"),
    ],
)
def test_numbers_are_exact(raw: str, comma: bool, expected: str) -> None:
    assert parse_decimal(raw, decimal_comma=comma) == Decimal(expected)


@pytest.mark.parametrize(
    ("raw", "comma"),
    [
        ("12,5", False),
        ("1,23,456", False),
        ("1.5", True),
        ("abc", False),
        ("=1+2", False),
        ("", False),
    ],
)
def test_ambiguous_or_malformed_numbers_are_rejected(raw: str, comma: bool) -> None:
    with pytest.raises(Exception, match="bad_number"):
        parse_decimal(raw, decimal_comma=comma)


def test_times_epoch_iso_and_local() -> None:
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo(_BERLIN)
    assert parse_time("1790000000000", None) == (1_790_000_000_000, False)
    assert parse_time("1790000000", None) == (1_790_000_000_000, False)
    assert parse_time("2026-09-21T14:13:20Z", None)[0] == 1_790_000_000_000
    assert parse_time("2026-09-21T16:13:20+02:00", None)[0] == 1_790_000_000_000
    assert parse_time("21.09.2026 16:13:20", berlin) == (1_790_000_000_000, False)
    assert parse_time("2026/09/21 16:13:20", berlin)[0] == 1_790_000_000_000
    # fall back: 02:30 exists twice — kept (first occurrence) and flagged
    ms, ambiguous = parse_time("25.10.2026 02:30:00", berlin)
    assert ambiguous and ms == parse_time("2026-10-25T02:30:00+02:00", None)[0]
    # spring forward: 02:30 does not exist
    with pytest.raises(Exception, match="nonexistent_local_time"):
        parse_time("29.03.2026 02:30:00", berlin)
    with pytest.raises(Exception, match="bad_timestamp"):
        parse_time("179000000", None)


def test_columns_by_alias_mapping_and_refusals() -> None:
    cols = resolve_columns(
        [
            "Position ID",
            "Futures",
            "Direction",
            "Size",
            "Avg. Open Price",
            "Trading Fee",
            "Realized PnL",
            "Open Time",
            "Close Time",
        ],
        ImportKind.POSITIONS,
    )
    assert cols["qty"] == 3 and cols["pnl"] == 6
    with pytest.raises(Exception, match="missing_column: position_id"):
        resolve_columns(["Symbol", "Side"], ImportKind.POSITIONS)
    two_pnl = ["Trade ID", "Pair", "Side", "Size", "Entry", "Open Date", "PnL", "Net PnL"]
    with pytest.raises(Exception, match="ambiguous_column: pnl"):
        resolve_columns(two_pnl, ImportKind.JOURNAL)
    mapped = resolve_columns(two_pnl, ImportKind.JOURNAL, {"pnl": "Net PnL"})
    assert mapped["pnl"] == 7
    with pytest.raises(Exception, match="unknown_mapping"):
        resolve_columns(two_pnl, ImportKind.JOURNAL, {"profit": "PnL"})


# ------------------------------------------------- venue exports vs the API


async def test_csv_import_equals_the_api_and_never_overrides_it(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    api = LiveArchive.open(tmp_path / "api.sqlite", now_ms=NOW_MS)
    await SyncService(
        make_client(venue, FakeTime()),
        api,
        venue="primary",
        account="main",
        source="simulated",
        now_ms=_now,
    ).run()
    csv_only = LiveArchive.open(tmp_path / "csv.sqlite", now_ms=NOW_MS)
    pos_csv = venue_positions_csv(venue, tmp_path / "positions.csv")
    fills_csv = venue_fills_csv(venue, tmp_path / "fills.csv")
    r1 = import_csv(csv_only, pos_csv, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    r2 = import_csv(
        csv_only, fills_csv, ImportOptions(ImportKind.FILLS, pnl_basis=PnlBasis.GROSS), now_ms=_now
    )
    assert (r1.status, r2.status) == (RunStatus.OK, RunStatus.OK)
    assert r1.pnl_basis is PnlBasis.GROSS and r1.pnl_basis_source == "inferred"
    # every closed position the API delivered is identical when it comes from the export
    from_api = {k: p for k, p in _positions(api).items() if p.status == "closed"}
    # (exports carry no liquidation size)
    expected = {k: dataclasses.replace(p, liq_qty=None) for k, p in from_api.items()}
    assert {k: p for k, p in _positions(csv_only).items() if k in from_api} == expected

    # the same file into the API-filled archive: all unchanged, nothing overwritten
    again = import_csv(api, pos_csv, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    assert again.counts[Outcome.UPDATED] == 0 and again.conflicts == 0
    assert again.counts[Outcome.UNCHANGED] == len(from_api)
    # a doctored export: reported, the API's record stays
    text = pos_csv.read_text().splitlines()
    first = text[-1].split(",")  # a position inside the API's look-back
    first[6] = str(Decimal(first[6]) + 1)  # fee
    doctored = _write(tmp_path, "doctored.csv", "\n".join([text[0], ",".join(first)]) + "\n")
    report = import_csv(
        api, doctored, ImportOptions(ImportKind.POSITIONS, pnl_basis=PnlBasis.GROSS), now_ms=_now
    )
    assert report.conflicts == 1 and report.status is RunStatus.PARTIAL
    assert IssueCode.CONFLICTS_WITH_API.value in _codes(report)
    assert _positions(api)[first[0]] == from_api[first[0]]


def test_localized_export_needs_a_timezone_and_converts_everything(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    reference = _archive(tmp_path)
    import_csv(
        reference,
        venue_positions_csv(venue, tmp_path / "api.csv"),
        ImportOptions(ImportKind.POSITIONS),
        now_ms=_now,
    )
    path = venue_positions_csv(venue, tmp_path / "local.csv", style="localized")
    other = LiveArchive.open(tmp_path / "other.sqlite", now_ms=NOW_MS)
    refused = import_csv(other, path, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    assert refused.refused is IssueCode.TIMEZONE_REQUIRED and refused.status is RunStatus.FAILED
    assert other.count(Entity.POSITION, venue="primary", account="main") == 0
    run = other.runs(venue="primary", account="main")[0]
    assert (run.status, run.error_code) == ("failed", "timezone_required")

    ok = import_csv(other, path, ImportOptions(ImportKind.POSITIONS, tz=_BERLIN), now_ms=_now)
    assert ok.status is RunStatus.OK
    assert ok.delimiter == ";" and ok.decimal_comma and ok.fee_sign_flipped
    assert ok.pnl_basis is PnlBasis.NET and ok.pnl_basis_source == "inferred"
    assert {IssueCode.FEE_SIGN_FLIPPED.value, IssueCode.PNL_BASIS_INFERRED.value} <= set(_codes(ok))
    ref, got = _positions(reference), _positions(other)
    for key, p in ref.items():
        g = got[key]
        # fee back to a positive cost, net P/L back to the venue's gross figure, times exact
        assert (g.fee, g.funding, g.realized_pnl) == (p.fee, p.funding, p.realized_pnl)
        assert (g.opened_ms // 1000, g.updated_ms // 1000) == (
            p.opened_ms // 1000,
            p.updated_ms // 1000,
        )
    # the localized file lacks leverage and margin mode: re-importing it over the
    # full record keeps them instead of reporting a change
    over = import_csv(reference, path, ImportOptions(ImportKind.POSITIONS, tz=_BERLIN), now_ms=_now)
    assert over.counts[Outcome.UPDATED] == 0


def test_reimport_is_idempotent_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    path = venue_positions_csv(venue, tmp_path / "p.csv")
    db = tmp_path / "live.sqlite"
    a = LiveArchive.open(db, now_ms=NOW_MS)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    dry = import_csv(a, path, ImportOptions(ImportKind.POSITIONS), now_ms=_now, dry_run=True)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert a.runs(venue="primary", account="main") == []
    first = import_csv(a, path, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    assert dict(dry.counts) == dict(first.counts) == {Outcome.INSERTED: len(venue.history)}
    second = import_csv(a, path, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    assert dict(second.counts) == {Outcome.UNCHANGED: len(venue.history)}
    assert IssueCode.ALREADY_IMPORTED.value in _codes(second)
    revisions = a._conn.execute("SELECT COUNT(*) FROM lh_revisions").fetchone()[0]
    assert revisions == len(venue.history)
    runs = a.runs(venue="primary", account="main")
    assert [r.kind for r in runs] == ["import", "import"]
    assert runs[0].source == f"csv:positions:{first.file_sha256[:12]}"


def test_duplicates_in_a_file(tmp_path: Path) -> None:
    text = "\n".join(
        [
            _POS_HEADER,
            _pos_row("P1"),
            _pos_row("P1", fee="0.730"),  # same meaning, other spelling → duplicate
            _pos_row("P2"),
            _pos_row("P2", fee="0.99"),  # same id, different content → both rejected
            _pos_row("P3", ctime=_T0 + 1000),
        ]
    )
    a = _archive(tmp_path)
    r = import_csv(
        a,
        _write(tmp_path, "d.csv", text + "\n"),
        ImportOptions(ImportKind.POSITIONS, pnl_basis=PnlBasis.GROSS),
        now_ms=_now,
    )
    assert r.duplicates_in_file == 1
    assert r.rejected == 2 and r.status is RunStatus.PARTIAL
    assert sorted(_positions(a)) == ["P1", "P3"]
    lines = sorted(i.line for i in r.issues if i.code is IssueCode.CONFLICTING_DUPLICATE)
    assert lines == [4, 5]
    stored = a.issues(a.runs(venue="primary", account="main")[0].id)
    assert (3, "duplicate_in_file", "info", None) in stored


def test_bad_rows_are_rejected_with_line_code_and_field_only(tmp_path: Path) -> None:
    rows = [
        _pos_row("OK1"),
        _pos_row(
            "",
        ),  # missing id
        _pos_row("B2", entryPrice="abc"),
        _pos_row("B3", maxQty="0"),
        _pos_row("B4", ctime="1400000000000", mtime="1400000003600"),  # 2014
        _pos_row("B5", ctime=NOW_MS + DAY_MS, mtime=NOW_MS + 2 * DAY_MS),  # future
        _pos_row("B6", mtime=_T0 - 1),  # closed before opened
        _pos_row("B7", side="SIDEWAYS"),
        _pos_row("=cmd|' /C calc'!A0"),  # a spreadsheet formula as an id
        _pos_row("B9", fee=""),  # a required amount left empty
        _pos_row("B10") + ",extra",
    ]
    a = _archive(tmp_path)
    r = import_csv(
        a,
        _write(tmp_path, "bad.csv", "\n".join([_POS_HEADER, *rows]) + "\n"),
        ImportOptions(ImportKind.POSITIONS, pnl_basis=PnlBasis.GROSS),
        now_ms=_now,
    )
    got = {(i.line, i.code.value, i.field) for i in r.issues}
    assert got >= {
        (3, "missing_value", "position_id"),
        (4, "bad_number", "entry_price"),
        (5, "not_positive", "qty"),
        (6, "implausible_time", "opened"),
        (7, "future_time", "opened"),
        (8, "closed_before_opened", "closed"),
        (9, "bad_choice", "side"),
        (10, "bad_text", "position_id"),
        (11, "missing_value", "fee"),
        (12, "wrong_column_count", None),
    }
    assert r.rejected == 10 and list(_positions(a)) == ["OK1"]
    detail = a.runs(venue="primary", account="main")[0].detail
    # nothing of a cell's content reaches the archive
    assert "calc" not in detail and "SIDEWAYS" not in detail
    assert "calc" not in json.dumps(a.issues(a.runs(venue="primary", account="main")[0].id))


def test_fees_funding_and_foreign_fee_currency(tmp_path: Path) -> None:
    header = _POS_HEADER + ",feeCoin"
    rows = [
        _pos_row("P1", fee="-0.73") + ",USDT",
        _pos_row("P2", fee="0.5", ctime=_T0 + 5000) + ",USDT",
        _pos_row("P3", ctime=_T0 + 9000) + ",BNB",
    ]
    a = _archive(tmp_path)
    r = import_csv(
        a,
        _write(tmp_path, "f.csv", "\n".join([header, *rows]) + "\n"),
        ImportOptions(ImportKind.POSITIONS, pnl_basis=PnlBasis.GROSS),
        now_ms=_now,
    )
    codes = _codes(r)
    assert IssueCode.MIXED_FEE_SIGNS.value in codes  # a rebate or a debit? not guessed
    assert (4, IssueCode.FOREIGN_FEE_CURRENCY) in {(i.line, i.code) for i in r.issues}
    assert _positions(a)["P1"].fee == Decimal("-0.73")
    # the file declares that fees are negative debits and funding is shown as paid-positive
    b = LiveArchive.open(tmp_path / "b.sqlite", now_ms=NOW_MS)
    rows2 = [_pos_row("P1", fee="-0.73", funding="0.2")]
    import_csv(
        b,
        _write(tmp_path, "g.csv", "\n".join([_POS_HEADER, *rows2]) + "\n"),
        ImportOptions(
            ImportKind.POSITIONS,
            pnl_basis=PnlBasis.GROSS,
            fee_sign="cost_negative",
            funding_sign="paid_positive",
        ),
        now_ms=_now,
    )
    p = _positions(b)["P1"]
    assert (p.fee, p.funding) == (Decimal("0.73"), Decimal("-0.2"))


def test_pnl_definitions(tmp_path: Path) -> None:
    # gross 10, fee 0.73, funding -0.2 → net_of_fees 9.27, net 9.07
    def file(name: str, pnl: str, n: int = 6, funding: bool = True) -> Path:
        hdr = _POS_HEADER if funding else _POS_HEADER.replace(",funding", "")
        rows = []
        for k in range(n):
            row = _pos_row(f"P{k}", realizedPNL=pnl, ctime=_T0 + k * 10_000, mtime=_T0 + 3_600_000)
            if not funding:
                parts = row.split(",")
                del parts[7]
                row = ",".join(parts)
            rows.append(row)
        return _write(tmp_path, name, "\n".join([hdr, *rows]) + "\n")

    for pnl, basis in (
        ("10", PnlBasis.GROSS),
        ("9.27", PnlBasis.NET_OF_FEES),
        ("9.07", PnlBasis.NET),
    ):
        a = LiveArchive.open(tmp_path / f"{basis}.sqlite", now_ms=NOW_MS)
        r = import_csv(
            a, file(f"{basis}.csv", pnl), ImportOptions(ImportKind.POSITIONS), now_ms=_now
        )
        assert r.pnl_basis is basis and r.pnl_basis_source == "inferred"
        assert {p.realized_pnl for p in _positions(a).values()} == {Decimal("10")}
    # declared against the evidence: kept, but reported
    a = LiveArchive.open(tmp_path / "doubt.sqlite", now_ms=NOW_MS)
    r = import_csv(
        a,
        file("doubt.csv", "9.07"),
        ImportOptions(ImportKind.POSITIONS, pnl_basis=PnlBasis.GROSS),
        now_ms=_now,
    )
    assert r.pnl_basis_source == "declared" and IssueCode.PNL_BASIS_DOUBTFUL.value in _codes(r)
    # rows that fit no basis: refused rather than guessed
    a = LiveArchive.open(tmp_path / "none.sqlite", now_ms=NOW_MS)
    r = import_csv(a, file("none.csv", "3.5"), ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    assert r.refused is IssueCode.PNL_BASIS_REQUIRED
    # net P/L without a funding column: the venue's gross figure is unknowable
    a = LiveArchive.open(tmp_path / "nofund.sqlite", now_ms=NOW_MS)
    r = import_csv(
        a,
        file("nofund.csv", "9.27", funding=False),
        ImportOptions(ImportKind.POSITIONS, pnl_basis=PnlBasis.NET),
        now_ms=_now,
    )
    assert r.refused is IssueCode.NET_PNL_WITHOUT_FUNDING
    # fills carry no prices to infer from: the basis must be declared
    a = LiveArchive.open(tmp_path / "fills.sqlite", now_ms=NOW_MS)
    venue = SimulatedVenue(now_ms=NOW_MS)
    r = import_csv(
        a,
        venue_fills_csv(venue, tmp_path / "fills.csv"),
        ImportOptions(ImportKind.FILLS),
        now_ms=_now,
    )
    assert r.refused is IssueCode.PNL_BASIS_REQUIRED
    r = import_csv(
        a,
        tmp_path / "fills.csv",
        ImportOptions(ImportKind.FILLS, pnl_basis=PnlBasis.NET_OF_FEES),
        now_ms=_now,
    )
    fill = next(
        f
        for f in a.records(Entity.FILL, venue="primary", account="main")
        if isinstance(f, Fill) and f.realized_pnl != 0
    )
    raw = next(t for t in venue.trades if t["tradeId"] == fill.trade_id)
    assert fill.realized_pnl == Decimal(raw["realizedPNL"]) + Decimal(raw["fee"])


def test_infer_needs_rows_that_tell_the_bases_apart() -> None:
    rows = [
        {
            "side": "long",
            "qty": Decimal(1),
            "entry_price": Decimal(100),
            "close_price": Decimal(110),
            "fee": Decimal(0),
            "funding": Decimal(0),
            "pnl": Decimal(10),
        }
    ]
    assert infer_pnl_basis(rows) is None  # no fee, no funding: all three fit


def test_file_level_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = _archive(tmp_path)
    opts = ImportOptions(ImportKind.POSITIONS, pnl_basis=PnlBasis.GROSS)
    assert (
        import_csv(a, _write(tmp_path, "e.csv", "  \n"), opts, now_ms=_now).refused
        is IssueCode.EMPTY_FILE
    )
    binary = tmp_path / "x.csv"
    binary.write_bytes(b"\x00\x01PK")
    assert import_csv(a, binary, opts, now_ms=_now).refused is IssueCode.NOT_TEXT
    wide = ",".join(f"c{i}" for i in range(80))
    assert (
        import_csv(a, _write(tmp_path, "w.csv", wide + "\n"), opts, now_ms=_now).refused
        is IssueCode.TOO_MANY_COLUMNS
    )
    monkeypatch.setattr("jarvis.live_history.csv_import.MAX_FILE_BYTES", 10)
    big = _write(tmp_path, "big.csv", _POS_HEADER + "\n" + _pos_row() + "\n")
    assert import_csv(a, big, opts, now_ms=_now).refused is IssueCode.FILE_TOO_LARGE
    assert MAX_FILE_BYTES == 20 * 1024 * 1024
    monkeypatch.setattr("jarvis.live_history.csv_import.MAX_FILE_BYTES", 10**9)
    monkeypatch.setattr("jarvis.live_history.csv_import.MAX_ROWS", 2)
    many = _write(
        tmp_path,
        "many.csv",
        "\n".join([_POS_HEADER, *(_pos_row(f"P{i}", ctime=_T0 + i) for i in range(3))]),
    )
    assert import_csv(a, many, opts, now_ms=_now).refused is IssueCode.TOO_MANY_ROWS
    assert a.count(Entity.POSITION, venue="primary", account="main") == 0
    assert all(r.status == "failed" for r in a.runs(venue="primary", account="main"))
    bad_tz = ImportOptions(ImportKind.POSITIONS, tz="Mars/Olympus")
    assert import_csv(a, big, bad_tz, now_ms=_now).refused is IssueCode.BAD_TIMEZONE


def test_cp1252_file_is_read_and_flagged(tmp_path: Path) -> None:
    header = "Trade ID,Pair,Direction,Size,Entry,Open Date,Notiz"
    row = f"J1,BTC/USDT,long,0.01,60000,{_T0},caf\u00e9 r\u00e9sum\u00e9"
    path = _write(tmp_path, "j.csv", f"{header}\n{row}\n", encoding="cp1252")
    r = import_csv(_archive(tmp_path), path, ImportOptions(ImportKind.JOURNAL), now_ms=_now)
    assert r.encoding == "cp1252" and IssueCode.ENCODING_FALLBACK.value in _codes(r)
    assert r.status is RunStatus.OK


def test_coverage_only_for_a_clean_declared_span(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS, days=150)
    a = _archive(tmp_path)
    since = NOW_MS - 150 * DAY_MS
    span = (since, NOW_MS - FIRST_LOOKBACK_MS)
    path = venue_positions_csv(venue, tmp_path / "p.csv")
    import_csv(a, path, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    assert a.coverage(venue="primary", account="main", stream=Stream.POSITIONS) == []
    import_csv(a, path, ImportOptions(ImportKind.POSITIONS, covers=span), now_ms=_now)
    assert a.coverage(venue="primary", account="main", stream=Stream.POSITIONS) == [span]
    plan = plan_backfill(a, venue="primary", account="main", since_ms=since, now_ms=NOW_MS)
    assert all(not (w.stream is Stream.POSITIONS and w.from_ms < span[1]) for w in plan)


# ------------------------------------------------------------------ journal


def test_journal_import_and_reconciliation(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    a = _archive(tmp_path)
    import_csv(
        a,
        venue_positions_csv(venue, tmp_path / "p.csv"),
        ImportOptions(ImportKind.POSITIONS),
        now_ms=_now,
    )
    ids = sorted(_positions(a))
    changes = JournalChanges(
        omit={ids[0]},
        extra=True,
        fee={ids[1]: Decimal("9.99")},
        pnl_offset={ids[2]: Decimal("1.50")},
        funding={ids[3]: Decimal("0")},
        duplicate={ids[4]},
    )
    path = journal_csv(venue, tmp_path / "journal.csv", changes)
    r = import_csv(a, path, ImportOptions(ImportKind.JOURNAL), now_ms=_now)
    assert r.status is RunStatus.OK and r.duplicates_in_file == 1
    assert r.pnl_basis is PnlBasis.NET and r.pnl_basis_source == "inferred"
    entries = a.journal_entries("control")
    assert len(entries) == len(ids)  # one omitted, one extra
    assert a.journals() == ["control"]
    result = reconcile_journal(_positions(a).values(), entries)
    found = {(f.code, f.position_id) for f in result.findings}
    assert (Code.MISSING_IN_JOURNAL, ids[0]) in found
    assert (Code.FEE_MISMATCH, ids[1]) in found
    assert (Code.PNL_MISMATCH, ids[2]) in found
    assert (Code.FUNDING_MISMATCH, ids[3]) in found
    assert any(f.code is Code.MISSING_AT_VENUE and f.entry_id == "J-9999" for f in result.findings)
    assert len(result.findings) == 5
    # journal entries never become venue records
    assert a.count(Entity.POSITION, venue="primary", account="main") == len(ids)
    again = import_csv(a, path, ImportOptions(ImportKind.JOURNAL), now_ms=_now)
    assert dict(again.counts) == {Outcome.UNCHANGED: len(ids)}


def test_journal_without_ids_gets_stable_keys(tmp_path: Path) -> None:
    header = "Pair,Direction,Size,Entry,Open Date"
    rows = [f"BTC/USDT,long,0.01,60000,{_T0}", f"ETH/USDT,short,0.5,3000,{_T0 + 1000}"]
    path = _write(tmp_path, "j.csv", "\n".join([header, *rows]) + "\n")
    a = _archive(tmp_path)
    import_csv(a, path, ImportOptions(ImportKind.JOURNAL), now_ms=_now)
    keys = [k for (k,) in a._conn.execute("SELECT entry_key FROM lh_journal_entries")]
    assert len(keys) == 2 and all(k.startswith("h:") for k in keys)
    again = import_csv(a, path, ImportOptions(ImportKind.JOURNAL), now_ms=_now)
    assert dict(again.counts) == {Outcome.UNCHANGED: 2}


# ---------------------------------------------------------------------- CLI


def test_cli_import(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    path = venue_positions_csv(venue, tmp_path / "p.csv", style="localized")
    db = tmp_path / "live.sqlite"
    args = ["import", "--archive", str(db), "--file", str(path), "--kind", "positions"]
    assert cli.main([*args, "--dry-run", "--tz", _BERLIN]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not db.exists()
    assert cli.main(args) == 2  # refused: no time zone
    assert json.loads(capsys.readouterr().out)["refused"] == "timezone_required"
    assert cli.main([*args, "--tz", _BERLIN, "--map", "qty=Size"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["changes"] == {"inserted": len(venue.history)}
    assert cli.main([*args, "--map", "nonsense"]) == 2
