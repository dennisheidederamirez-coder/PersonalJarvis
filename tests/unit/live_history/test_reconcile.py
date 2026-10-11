"""Reconciliation: venue-internal sums, journal matching on aligned definitions, the balance."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from jarvis.live_history.archive import LiveArchive
from jarvis.live_history.models import Balance, Entity, Fill, Position
from jarvis.live_history.reconcile import (
    Code,
    JournalEntry,
    PnlBasis,
    check_balance,
    check_internal,
    match,
    reconcile_journal,
)
from jarvis.live_history.simulated_venue import DAY_MS, SimulatedVenue
from jarvis.live_history.sync import SyncService
from tests.fakes.fake_live_venue import NOW_MS, FakeTime, make_client


async def _archive(tmp_path: Path) -> tuple[LiveArchive, SimulatedVenue]:
    venue = SimulatedVenue(now_ms=NOW_MS)
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS, origin="synthetic")
    await SyncService(
        make_client(venue, FakeTime()),
        archive,
        venue="primary",
        account="main",
        source="simulated",
        now_ms=lambda: NOW_MS,
    ).run()
    return archive, venue


def _positions(a: LiveArchive) -> list[Position]:
    return [
        p
        for p in a.records(Entity.POSITION, venue="primary", account="main")
        if isinstance(p, Position)
    ]


def _fills(a: LiveArchive) -> list[Fill]:
    return [
        f for f in a.records(Entity.FILL, venue="primary", account="main") if isinstance(f, Fill)
    ]


def _entry(p: Position, **kw: object) -> JournalEntry:
    """What a journal would hold for ``p``: other spelling, rounded, seconds later."""
    base: dict[str, object] = dict(
        entry_id=f"J-{p.position_id}",
        symbol=p.symbol[:-4] + "/USDT",
        side=p.side.upper(),
        qty=p.qty,
        entry_price=p.entry_price,
        opened_ms=p.opened_ms + 20_000,
        closed_ms=p.updated_ms + 15_000 if p.status == "closed" else None,
        exit_price=p.close_price,
        fee=p.fee.quantize(Decimal("0.01")),
        funding=p.funding.quantize(Decimal("0.01")),
        pnl=p.net.quantize(Decimal("0.01")) if p.status == "closed" else None,
        pnl_basis=PnlBasis.NET,
    )
    base.update(kw)
    return JournalEntry(**base)  # type: ignore[arg-type]


async def test_simulated_history_is_internally_consistent(tmp_path: Path) -> None:
    a, _ = await _archive(tmp_path)
    assert check_internal(_positions(a), _fills(a)) == []


async def test_internal_fee_and_pnl_differences_are_found(tmp_path: Path) -> None:
    a, _ = await _archive(tmp_path)
    ps = _positions(a)
    closed = next(p for p in ps if p.status == "closed")
    tampered = [
        replace(p, fee=p.fee + Decimal("0.5"), realized_pnl=p.realized_pnl - 1)
        if p is closed
        else p
        for p in ps
    ]
    codes = {(f.code, f.position_id) for f in check_internal(tampered, _fills(a))}
    assert codes == {
        (Code.FILL_FEE_MISMATCH, closed.position_id),
        (Code.FILL_PNL_MISMATCH, closed.position_id),
    }
    other_fills = [f for f in _fills(a) if f.position_id != closed.position_id]
    assert {f.code for f in check_internal(ps, other_fills)} == {Code.FILLS_MISSING}


async def test_a_faithful_journal_matches_completely(tmp_path: Path) -> None:
    a, _ = await _archive(tmp_path)
    ps = _positions(a)
    result = reconcile_journal(ps, [_entry(p) for p in reversed(ps)])
    assert result.findings == []
    assert {(p.position_id, e.entry_id) for p, e in result.pairs} == {
        (p.position_id, f"J-{p.position_id}") for p in ps
    }


async def test_missing_and_extra_entries(tmp_path: Path) -> None:
    a, _ = await _archive(tmp_path)
    ps = _positions(a)
    entries = [_entry(p) for p in ps[1:]]
    entries.append(
        _entry(ps[1], entry_id="J-ghost", opened_ms=ps[1].opened_ms + 30 * DAY_MS, closed_ms=None)
    )
    found = {(f.code, f.position_id or f.entry_id) for f in reconcile_journal(ps, entries).findings}
    assert (Code.MISSING_IN_JOURNAL, ps[0].position_id) in found
    assert (Code.MISSING_AT_VENUE, "J-ghost") in found


def _pos(pid: str, opened: int, **kw: object) -> Position:
    base: dict[str, object] = dict(
        venue="primary",
        account="main",
        position_id=pid,
        symbol="BTCUSDT",
        side="long",
        status="closed",
        qty=Decimal("0.01"),
        entry_price=Decimal("60000"),
        close_price=Decimal("61000"),
        fee=Decimal("0.72"),
        funding=Decimal("-0.1"),
        realized_pnl=Decimal("10"),
        unrealized_pnl=None,
        liq_qty=None,
        leverage=1,
        margin_mode="ISOLATION",
        opened_ms=opened,
        updated_ms=opened + 3_600_000,
    )
    base.update(kw)
    return Position(**base)  # type: ignore[arg-type]


def test_two_equally_good_candidates_are_never_guessed() -> None:
    p1, p2 = _pos("P1", NOW_MS), _pos("P2", NOW_MS)
    e = _entry(p1, entry_id="J1")
    result = match([p1, p2], [e])
    assert result.pairs == []
    assert {f.code for f in result.findings} == {Code.AMBIGUOUS_MATCH}
    assert {f.position_id for f in result.findings if f.position_id} == {"P1", "P2"}
    assert any(f.entry_id == "J1" for f in result.findings)


def test_a_clearly_better_candidate_wins() -> None:
    p1, p2 = _pos("P1", NOW_MS), _pos("P2", NOW_MS + 100_000)
    result = match([p1, p2], [_entry(p1, entry_id="J1", opened_ms=NOW_MS)])
    assert [(p.position_id, e.entry_id) for p, e in result.pairs] == [("P1", "J1")]
    assert [(f.code, f.position_id) for f in result.findings] == [(Code.MISSING_IN_JOURNAL, "P2")]


def test_side_or_symbol_never_match_across() -> None:
    p = _pos("P1", NOW_MS)
    assert match([p], [_entry(p, side="SHORT")]).pairs == []
    assert match([p], [_entry(p, symbol="ETH/USDT")]).pairs == []


def test_pnl_is_compared_on_the_journal_basis() -> None:
    p = _pos("P1", NOW_MS)  # realized 10 (gross), fee 0.72, funding -0.1 -> net 9.18
    for basis, value in [
        (PnlBasis.GROSS, "10"),
        (PnlBasis.NET_OF_FEES, "9.28"),
        (PnlBasis.NET, "9.18"),
    ]:
        assert (
            reconcile_journal([p], [_entry(p, pnl=Decimal(value), pnl_basis=basis)]).findings == []
        )
    wrong = reconcile_journal([p], [_entry(p, pnl=Decimal("10"), pnl_basis=PnlBasis.NET)]).findings
    assert [(f.code, f.venue_value, f.other_value) for f in wrong] == [
        (Code.PNL_MISMATCH, Decimal("9.18"), Decimal("10"))
    ]


def test_fee_funding_price_and_status_differences() -> None:
    p = _pos("P1", NOW_MS)
    entry = _entry(
        p,
        fee=Decimal("0.50"),
        funding=Decimal("0"),
        exit_price=Decimal("61100"),
        closed_ms=None,
        pnl=None,
    )
    codes = {f.code for f in reconcile_journal([p], [entry]).findings}
    assert codes == {
        Code.FEE_MISMATCH,
        Code.FUNDING_MISMATCH,
        Code.PRICE_MISMATCH,
        Code.STATUS_MISMATCH,
    }


def test_rounding_inside_tolerance_is_not_a_difference() -> None:
    p = _pos("P1", NOW_MS, fee=Decimal("0.7234"))
    assert reconcile_journal([p], [_entry(p, fee=Decimal("0.72"))]).findings == []


async def test_balance_change_is_explained_by_the_trades(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    archive = LiveArchive.open(tmp_path / "live.sqlite", now_ms=NOW_MS, origin="synthetic")
    t1 = NOW_MS

    def service(now: int) -> SyncService:
        return SyncService(
            make_client(venue, FakeTime()),
            archive,
            venue="primary",
            account="main",
            source="simulated",
            now_ms=lambda: now,
        )

    await service(t1).run()
    t2 = NOW_MS + 3 * DAY_MS
    venue.now_ms = t2
    venue.close_open_position(t2 - DAY_MS, Decimal("63000"))
    await service(t2).run()
    balances = [
        b
        for b in archive.records(Entity.BALANCE, venue="primary", account="main")
        if isinstance(b, Balance)
    ]
    before, after = balances
    p_before = archive.positions_as_of(t1, venue="primary", account="main")
    p_after = archive.positions_as_of(t2, venue="primary", account="main")
    assert after.wallet != before.wallet
    assert check_balance(before, after, p_before, p_after) == []
    deposit = replace(after, available=after.available + Decimal("250"))
    [finding] = check_balance(before, deposit, p_before, p_after)
    assert finding.code is Code.UNEXPLAINED_BALANCE_CHANGE
    assert finding.difference == Decimal("250")
