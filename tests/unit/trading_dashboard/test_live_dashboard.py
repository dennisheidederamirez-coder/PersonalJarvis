"""The LIVE history in the trading dashboard: read-only, never mixed with paper, exact sums."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import tomllib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.config import JarvisConfig
from jarvis.core.config_writer import set_trading_dashboard_live_archive_path
from jarvis.live_history.archive import LiveArchive
from jarvis.live_history.models import Position
from jarvis.trading_dashboard import live_views as lv
from jarvis.trading_dashboard.live_reader import LiveArchiveReader, LiveSnapshot
from jarvis.trading_dashboard.reader import JournalReader, ReadFailure, SourceState
from tests.fakes.fake_live_archive import DEPOSIT, DemoArchive, build_demo_archive
from tests.fakes.fake_paper_journal import PaperRun, run_paper_test

LIVE = ("overview", "positions", "trades", "costs", "reconciliation", "sources")


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> DemoArchive:
    return build_demo_archive(tmp_path_factory.mktemp("live"))


@pytest.fixture(scope="module")
def paper(tmp_path_factory: pytest.TempPathFactory) -> PaperRun:
    return run_paper_test(tmp_path_factory.mktemp("paper"), days=2)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ctx(demo: DemoArchive) -> lv.LiveContext:
    snap = LiveArchiveReader(demo.path).read()
    assert isinstance(snap, LiveSnapshot)
    return lv.context(snap, demo.now_ms)


def _client(data_dir: Path, *, journal: str = "", live: str = "") -> TestClient:
    from jarvis.ui.web.trading_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(data_dir)),
        trading_dashboard=SimpleNamespace(journal_path=journal, live_archive_path=live),
    )
    return TestClient(app)


# ------------------------------------------------------------------ reader


def test_reading_never_changes_the_archive(demo: DemoArchive) -> None:
    before, mtime = _digest(demo.path), demo.path.stat().st_mtime_ns
    reader = LiveArchiveReader(demo.path)
    for _ in range(3):
        assert isinstance(reader.read(), LiveSnapshot)
    assert _digest(demo.path) == before and demo.path.stat().st_mtime_ns == mtime
    assert not list(demo.path.parent.glob("live_history.sqlite-*"))  # no -journal / -wal left


def test_the_connection_refuses_writes(demo: DemoArchive) -> None:
    conn = LiveArchiveReader(demo.path)._connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM lh_records")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE t (x)")
    finally:
        conn.close()


def test_missing_paper_and_foreign_files(tmp_path: Path, paper: PaperRun) -> None:
    missing = LiveArchiveReader(tmp_path / "nope.sqlite").read()
    assert isinstance(missing, ReadFailure) and missing.state is SourceState.MISSING
    assert not (tmp_path / "nope.sqlite").exists()
    as_live = LiveArchiveReader(paper.path).read()
    assert isinstance(as_live, ReadFailure) and as_live.state is SourceState.INVALID
    foreign = tmp_path / "other.sqlite"
    with sqlite3.connect(foreign) as c:
        c.execute("CREATE TABLE lh_meta (key TEXT, value TEXT)")
        c.execute("CREATE TABLE lh_records (x)")
        c.execute("INSERT INTO lh_meta VALUES ('mode', 'paper')")
    bad = LiveArchiveReader(foreign).read()
    assert isinstance(bad, ReadFailure) and bad.state is SourceState.INVALID


def test_the_paper_reader_rejects_the_live_archive(demo: DemoArchive) -> None:
    result = JournalReader(demo.path).read()
    assert isinstance(result, ReadFailure) and result.state is SourceState.INVALID


def test_a_writer_holding_the_lock_gives_busy_not_a_hang(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    LiveArchive.open(path, now_ms=1, origin="synthetic").close()
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        started = time.monotonic()
        result = LiveArchiveReader(path, busy_ms=50, sleep=lambda _: None).read()
        assert isinstance(result, ReadFailure) and result.state is SourceState.BUSY
        assert time.monotonic() - started < 2
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert isinstance(LiveArchiveReader(path).read(), LiveSnapshot)


def test_a_sync_is_not_blocked_by_readers(demo: DemoArchive, tmp_path: Path) -> None:
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes(demo.path.read_bytes())
    stop = threading.Event()

    def read_loop() -> None:
        reader = LiveArchiveReader(copy)
        while not stop.is_set():
            reader._cache = None
            reader.read()

    t = threading.Thread(target=read_loop)
    t.start()
    try:
        archive = LiveArchive.open(copy, now_ms=demo.now_ms, origin="synthetic")
        started = time.monotonic()
        for i in range(20):
            with archive.batch():
                archive.begin_run(
                    venue="primary", account="main", kind="sync", source="t", now_ms=i
                )
        assert time.monotonic() - started < 5
        archive.close()
    finally:
        stop.set()
        t.join()


# ------------------------------------------------------------------- views


def test_overview_totals_follow_the_definitions(demo: DemoArchive) -> None:
    ctx = _ctx(demo)
    o = lv.overview(ctx)
    snap = ctx.snap
    assert isinstance(snap, LiveSnapshot)
    closed = [p for p in snap.positions if p.status == "closed"]
    realized = sum((p.realized_pnl for p in closed), Decimal(0))
    fees = sum((p.fee for p in closed), Decimal(0))
    funding = sum((p.funding for p in closed), Decimal(0))
    assert o.totals.closed == len(closed) == len(demo.venue.history)
    assert o.totals.realized == pytest.approx(float(realized))
    assert o.totals.fees == pytest.approx(float(fees))
    assert o.totals.funding == pytest.approx(float(funding))
    # net = realized − fees + funding, and the curve ends there
    assert o.totals.net == pytest.approx(float(realized - fees + funding))
    assert o.pnl_series[-1].cumulative == pytest.approx(o.totals.net)
    assert o.totals.wins + o.totals.losses <= o.totals.closed
    # three syncs, three observations; the last includes the deposit
    assert len(o.balance_series) == 3 and o.margin_coin == "USDT"
    assert o.balance is not None and o.balance.wallet == pytest.approx(float(demo.venue.wallet))
    assert o.last_run is not None and o.last_run.kind == "import"
    assert o.findings == {"error": 4, "warning": 3}


def test_reconciliation_finds_exactly_the_planted_differences(demo: DemoArchive) -> None:
    r = lv.reconciliation(_ctx(demo))
    by_code = {(f.code, f.position_id) for f in r.findings}
    for code, pid in demo.journal_changes.items():
        assert (code, pid) in by_code, code
    assert ("fill_fee_mismatch", demo.doctored_fill_position) in by_code
    extra = [f for f in r.findings if f.code == "missing_at_venue"]
    assert [f.entry_id for f in extra] == ["J-9999"]
    balance = [f for f in r.findings if f.group == "balance"]
    assert len(balance) == 1 and balance[0].difference == pytest.approx(float(DEPOSIT))
    assert r.balance_checks == 2
    assert r.journal == "control" and r.pnl_basis == "net"
    assert sum(r.counts.values()) == len(r.findings) == 7
    # errors first
    assert [f.severity for f in r.findings][:4] == ["error"] * 4
    # an unknown journal falls back to the default one
    assert lv.reconciliation(_ctx(demo), journal="nope").journal == "control"


def test_positions_trades_and_costs(demo: DemoArchive) -> None:
    ctx = _ctx(demo)
    page = lv.positions(ctx, limit=5)
    assert len(page.closed) == 5 and page.total_closed == len(demo.venue.history)
    closes = [p.closed_ms for p in page.closed]
    assert closes == sorted(closes, reverse=True)
    nxt = lv.positions(ctx, limit=5, offset=5)
    assert not {p.position_id for p in page.closed} & {p.position_id for p in nxt.closed}
    btc = lv.positions(ctx, symbol="BTCUSDT", limit=500)
    assert btc.closed and {p.symbol for p in btc.closed} == {"BTCUSDT"}
    origins = {p.origin for p in lv.positions(ctx, limit=500).closed}
    assert origins == {"api", "csv"}  # recent from the API, old from the export
    flagged = {p.position_id: p.findings for p in lv.positions(ctx, limit=500).closed}
    assert flagged[demo.journal_changes["fee_mismatch"]] == ["fee_mismatch"]

    trades = lv.trades(ctx, limit=10)
    assert trades.total == len(demo.venue.trades) and len(trades.fills) == 10
    stamps = [f.ts_ms for f in trades.fills]
    assert stamps == sorted(stamps, reverse=True)
    all_fees = sum(Decimal(t["fee"]) for t in demo.venue.trades) + Decimal("0.40")
    assert trades.fees == pytest.approx(float(all_fees))

    costs = lv.costs(ctx)
    for rows in (costs.by_month, costs.by_symbol):
        assert sum(r.fees for r in rows) == pytest.approx(costs.totals.fees)
        assert sum(r.funding for r in rows) == pytest.approx(costs.totals.funding)
        assert sum(r.net for r in rows) == pytest.approx(costs.totals.net)
        assert sum(r.positions for r in rows) == costs.totals.closed
        for r in rows:
            assert r.funding == pytest.approx(r.funding_paid + r.funding_received)
            assert r.funding_paid <= 0 <= r.funding_received
    assert costs.taker_fills == len(demo.venue.trades)


def test_sources_show_runs_imports_issues_and_coverage(demo: DemoArchive) -> None:
    v = lv.sources(_ctx(demo))
    assert [r.kind for r in v.runs].count("sync") == 3
    kinds = [(i.kind, i.run.status) for i in v.imports]
    assert kinds == [
        ("journal", "ok"),
        ("positions", "ok"),
        ("fills", "ok"),
        ("fills", "partial"),
    ]
    broken = v.imports[-1]
    assert broken.issue_counts == {"bad_timestamp": 1, "bad_number": 1}
    assert {(i.line, i.field) for i in broken.issues} == {(4, "time"), (6, "price")}
    assert len(broken.file_digest) == 12 and "csv" not in broken.file_digest
    assert v.imports[1].pnl_basis == "net" and v.imports[1].pnl_basis_source == "inferred"
    assert v.counts["journal_entry"] == lv.reconciliation(_ctx(demo)).entries_compared
    cov = {c.stream: c for c in v.coverage}
    assert len(cov["positions"].intervals) == 1 and not cov["positions"].gaps
    assert [g.route for g in cov["orders"].gaps] == ["export"]


def test_source_warnings(demo: DemoArchive, tmp_path: Path) -> None:
    assert lv.source(_ctx(demo)).warnings == []
    later = lv.context(LiveArchiveReader(demo.path).read(), demo.now_ms + 30 * lv.DAY_MS)
    assert lv.source(later).warnings == ["sync_old"]
    fresh = tmp_path / "fresh.sqlite"
    LiveArchive.open(fresh, now_ms=1, origin="synthetic").close()
    empty = lv.context(LiveArchiveReader(fresh).read(), demo.now_ms)
    assert lv.source(empty).warnings == ["no_sync_yet"]
    o = lv.overview(empty)
    assert o.totals.closed == 0 and o.balance is None and o.pnl_series == []


def test_a_failed_read_gives_empty_sections(tmp_path: Path) -> None:
    ctx = lv.context(LiveArchiveReader(tmp_path / "none.sqlite").read(), 1)
    assert lv.overview(ctx).source.state == "missing"
    assert lv.positions(ctx).closed == [] and lv.trades(ctx).fills == []
    assert lv.costs(ctx).by_month == [] and lv.reconciliation(ctx).findings == []
    assert lv.sources(ctx).runs == []


def test_backfilled_old_positions_do_not_explain_a_balance_change(tmp_path: Path) -> None:
    """A position imported between two observations but closed before both moved nothing."""
    demo = build_demo_archive(tmp_path)
    archive = LiveArchive.open(demo.path, now_ms=demo.now_ms, origin="synthetic")
    old = Position(
        venue="primary",
        account="main",
        position_id="OLD1",
        symbol="BTCUSDT",
        side="long",
        status="closed",
        qty=Decimal("0.01"),
        entry_price=Decimal("50000"),
        close_price=Decimal("51000"),
        fee=Decimal("0.6"),
        funding=Decimal("0"),
        realized_pnl=Decimal("10"),
        unrealized_pnl=None,
        liq_qty=None,
        leverage=None,
        margin_mode=None,
        opened_ms=1_700_000_000_000,
        updated_ms=1_700_000_100_000,
    )
    snap = LiveArchiveReader(demo.path).read()
    assert isinstance(snap, LiveSnapshot)
    between = snap.balances[1].ts_ms - 1000  # observed between the 1st and the 2nd balance
    with archive.batch():
        run = archive.begin_run(
            venue="primary", account="main", kind="import", source="csv:t", now_ms=between
        )
        archive.upsert(old, run_id=run, now_ms=between, source="csv:t")
    archive.close()
    ctx = lv.context(LiveArchiveReader(demo.path).read(), demo.now_ms)
    balance = [f for f in lv.reconciliation(ctx).findings if f.group == "balance"]
    assert len(balance) == 1 and balance[0].difference == pytest.approx(float(DEPOSIT))


# ------------------------------------------------------------------ routes


def test_live_routes_read_the_configured_archive(demo: DemoArchive, tmp_path: Path) -> None:
    before = _digest(demo.path)
    client = _client(tmp_path, live=str(demo.path))
    for name in LIVE:
        res = client.get(f"/api/trading/live/{name}")
        assert res.status_code == 200, name
        body = res.json()
        assert body["source"]["state"] == "ok", name
        assert body["source"]["file_name"] == demo.path.name
        assert str(demo.path.parent) not in res.text
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(f"/api/trading/live/{name}").status_code == 405
    assert _digest(demo.path) == before


def test_live_and_paper_never_mix(demo: DemoArchive, paper: PaperRun, tmp_path: Path) -> None:
    both = _client(tmp_path, journal=str(paper.path), live=str(demo.path))
    assert both.get("/api/trading/overview").json()["source"]["state"] == "ok"
    assert both.get("/api/trading/live/overview").json()["source"]["state"] == "ok"
    swapped = _client(tmp_path, journal=str(demo.path), live=str(paper.path))
    assert swapped.get("/api/trading/overview").json()["source"]["state"] == "invalid"
    live = swapped.get("/api/trading/live/overview").json()
    assert live["source"]["state"] == "invalid" and live["totals"]["closed"] == 0


def test_live_default_path_and_validation(demo: DemoArchive, tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.get("/api/trading/live/overview").json()
    assert body["source"]["state"] == "missing"
    assert body["source"]["file_name"] == "live_history.sqlite"
    assert not (tmp_path / "trading").exists()
    configured = _client(tmp_path, live=str(demo.path))
    assert (
        configured.get("/api/trading/live/trades", params={"symbol": "btc'--"}).status_code == 422
    )
    assert configured.get("/api/trading/live/positions", params={"limit": 0}).status_code == 422
    assert (
        configured.get("/api/trading/live/reconciliation", params={"journal": "../x"}).status_code
        == 422
    )
    page = configured.get("/api/trading/live/positions", params={"symbol": "ETHUSDT"}).json()
    assert {p["symbol"] for p in page["closed"]} == {"ETHUSDT"}


def test_the_live_setter_writes_only_its_key(tmp_path: Path) -> None:
    from jarvis.ui.web.trading_routes import live_archive_path

    toml = tmp_path / "jarvis.toml"
    toml.write_text('[trading_dashboard]\njournal_path = "/p.sqlite"\n', encoding="utf-8")
    set_trading_dashboard_live_archive_path("/data/live.sqlite", path=toml)
    data = tomllib.loads(toml.read_text(encoding="utf-8"))
    assert data["trading_dashboard"] == {
        "journal_path": "/p.sqlite",
        "live_archive_path": "/data/live.sqlite",
    }
    cfg = JarvisConfig.model_validate(data)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    assert live_archive_path(request) == Path("/data/live.sqlite")  # type: ignore[arg-type]
    default = JarvisConfig()
    default.memory.data_dir = str(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=default)))
    assert live_archive_path(request) == tmp_path / "trading" / "live_history.sqlite"  # type: ignore[arg-type]


# ------------------------------------------------------- traceability, origin


def test_every_finding_is_traceable(demo: DemoArchive) -> None:
    ctx = _ctx(demo)
    snap = ctx.snap
    assert isinstance(snap, LiveSnapshot)
    r = lv.reconciliation(ctx)
    assert len({f.id for f in r.findings}) == len(r.findings)
    by_code = {f.code: f for f in r.findings}

    internal = by_code["fill_fee_mismatch"]
    own = sorted(x.trade_id for x in snap.fills if x.position_id == internal.position_id)
    assert internal.fill_ids == own and len(own) >= 2
    assert (internal.field, internal.other_source, internal.venue_source) == ("fee", "fills", "csv")
    assert internal.opened_ms is not None and internal.closed_ms is not None
    assert internal.difference == pytest.approx(-0.40)

    pnl = by_code["pnl_mismatch"]
    assert (pnl.field, pnl.other_source, pnl.journal, pnl.basis) == (
        "pnl",
        "journal",
        "control",
        "net",
    )
    assert pnl.position_id and pnl.entry_id and pnl.side in ("long", "short")
    assert pnl.venue_source == "api"

    extra = by_code["missing_at_venue"]
    assert extra.position_id is None and extra.entry_id == "J-9999"
    assert extra.opened_ms is not None and extra.side == "short"  # from the journal entry

    balance = by_code["unexplained_balance_change"]
    assert (balance.field, balance.other_source) == ("wallet", "trade_history")
    assert (balance.period_from_ms, balance.period_to_ms) == (
        snap.balances[1].ts_ms,
        snap.balances[2].ts_ms,
    )


def test_the_archive_origin_reaches_the_dashboard(demo: DemoArchive, tmp_path: Path) -> None:
    assert lv.source(_ctx(demo)).origin == "synthetic"
    real = tmp_path / "real.sqlite"
    LiveArchive.open(real, now_ms=1, origin="real").close()
    src = lv.source(lv.context(LiveArchiveReader(real).read(), demo.now_ms))
    assert src.origin == "real" and "origin_unknown" not in src.warnings
    unlabelled = tmp_path / "old.sqlite"
    LiveArchive.open(unlabelled, now_ms=1, origin="real").close()
    with sqlite3.connect(unlabelled) as c:
        c.execute("DELETE FROM lh_meta WHERE key = 'origin'")
    src = lv.source(lv.context(LiveArchiveReader(unlabelled).read(), demo.now_ms))
    assert src.origin == "unknown" and "origin_unknown" in src.warnings
