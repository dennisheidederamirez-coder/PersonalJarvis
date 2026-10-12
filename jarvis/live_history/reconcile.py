"""Find differences: inside the venue's own data, against an external journal, in the balance.

Three checks, each returning ``Finding`` s with a stable ``code`` (the UI will
translate the codes; the amounts stay exact ``Decimal``):

- ``check_internal`` — a closed position against its own fills: the fills'
  fees and realized P/L must add up to the position's.
- ``match`` + ``compare`` — venue positions against entries of an external
  trading journal. The journal is a CONTROL source, not an independent one
  (it may have been filled from the same venue), and it usually has no venue
  ids, so entries are paired by canonical symbol and side (required) and by
  open/close time, size and entry price within tolerances. Every position
  and every entry is used at most once; when the best candidate is not
  clearly better than the next one, nothing is guessed — both sides are
  reported ``ambiguous_match`` for a person to decide.
- ``check_balance`` — the change of the wallet balance between two
  observations against the change of fees, funding and realized P/L of all
  positions in between. A remainder is a movement the trade history does not
  explain (a deposit, a transfer, a bonus …) and is reported, never absorbed.
- ``compare_twin`` — a position the API delivered against the same position
  from an export without ids (paired by symbol, side and opening second).
- ``check_fill_totals`` — the daily fill totals of an export without trade
  ids against the archive's own fills for days those cover completely, and
  otherwise against the realized P/L of the positions closed in the same
  days. Totals are never assigned to a single position.

Definitions are aligned before amounts are compared: the venue's realized P/L
EXCLUDES fees and funding; a journal entry says which basis its own P/L uses
(``gross``, ``net_of_fees`` or ``net``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from jarvis.live_history.models import Balance, Fill, FillTotal, Position
from jarvis.live_history.symbols import canonical


class Code(StrEnum):
    FILLS_MISSING = "fills_missing"
    FILL_FEE_MISMATCH = "fill_fee_mismatch"
    FILL_PNL_MISMATCH = "fill_pnl_mismatch"
    MISSING_IN_JOURNAL = "missing_in_journal"
    MISSING_AT_VENUE = "missing_at_venue"
    AMBIGUOUS_MATCH = "ambiguous_match"
    STATUS_MISMATCH = "status_mismatch"  # open on one side, closed on the other
    PRICE_MISMATCH = "price_mismatch"
    FEE_MISMATCH = "fee_mismatch"
    FUNDING_MISMATCH = "funding_mismatch"
    PNL_MISMATCH = "pnl_mismatch"
    UNEXPLAINED_BALANCE_CHANGE = "unexplained_balance_change"
    #: an export's stated net P/L ≠ its own gross − fee + funding
    EXPORT_PNL_INCONSISTENT = "export_pnl_inconsistent"
    EXPORT_DIFFERS_FROM_API = "export_differs_from_api"
    FILL_TOTALS_MISMATCH = "fill_totals_mismatch"


class Severity(StrEnum):
    ERROR = "error"  # numbers disagree
    WARNING = "warning"  # something is missing or needs a person
    INFO = "info"  # worth knowing, nothing wrong yet


class PnlBasis(StrEnum):
    GROSS = "gross"  # before fees and funding (the venue's own realized P/L)
    NET_OF_FEES = "net_of_fees"  # after fees, before funding
    NET = "net"  # after fees and funding


@dataclass(frozen=True)
class JournalEntry:
    """One trade as an external journal records it (normalized by its importer)."""

    entry_id: str
    symbol: str
    side: str  # long | short
    qty: Decimal
    entry_price: Decimal
    opened_ms: int
    closed_ms: int | None = None
    exit_price: Decimal | None = None
    fee: Decimal | None = None
    funding: Decimal | None = None
    pnl: Decimal | None = None
    pnl_basis: PnlBasis = PnlBasis.NET


@dataclass(frozen=True)
class Tolerances:
    amount_abs: Decimal = Decimal("0.01")  # USDT
    amount_rel: Decimal = Decimal("0.0005")
    time_ms: int = 120_000
    qty_rel: Decimal = Decimal("0.005")
    price_rel: Decimal = Decimal("0.0005")
    #: A best candidate must beat the runner-up by this score margin.
    ambiguity_margin: float = 0.25


DEFAULT_TOLERANCES: Final = Tolerances()


@dataclass(frozen=True)
class Finding:
    code: Code
    severity: Severity
    position_id: str | None = None
    entry_id: str | None = None
    symbol: str | None = None
    venue_value: Decimal | None = None
    other_value: Decimal | None = None
    #: the compared quantity, when the code alone does not say it
    field: str | None = None
    #: the time span a finding about totals covers (UTC ms, end exclusive)
    period: tuple[int, int] | None = None

    @property
    def difference(self) -> Decimal | None:
        if self.venue_value is None or self.other_value is None:
            return None
        return self.venue_value - self.other_value


@dataclass
class MatchResult:
    pairs: list[tuple[Position, JournalEntry]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def _differs(a: Decimal, b: Decimal, tol: Tolerances) -> bool:
    limit = max(tol.amount_abs, tol.amount_rel * max(abs(a), abs(b)))
    return abs(a - b) > limit


def _rel(a: Decimal, b: Decimal) -> Decimal:
    base = max(abs(a), abs(b))
    return Decimal(0) if base == 0 else abs(a - b) / base


# ----------------------------------------------------------------- internal


def check_internal(
    positions: Iterable[Position], fills: Iterable[Fill], tol: Tolerances = DEFAULT_TOLERANCES
) -> list[Finding]:
    by_position: dict[str, list[Fill]] = defaultdict(list)
    for f in fills:
        if f.position_id:
            by_position[f.position_id].append(f)
    out: list[Finding] = []
    for p in positions:
        if p.status != "closed":
            continue
        own = by_position.get(p.position_id, [])
        if not own:
            out.append(Finding(Code.FILLS_MISSING, Severity.INFO, p.position_id, symbol=p.symbol))
            continue
        fee = sum((f.fee for f in own), Decimal(0))
        pnl = sum((f.realized_pnl for f in own), Decimal(0))
        if _differs(p.fee, fee, tol):
            out.append(
                Finding(
                    Code.FILL_FEE_MISMATCH,
                    Severity.ERROR,
                    p.position_id,
                    symbol=p.symbol,
                    venue_value=p.fee,
                    other_value=fee,
                )
            )
        if _differs(p.realized_pnl, pnl, tol):
            out.append(
                Finding(
                    Code.FILL_PNL_MISMATCH,
                    Severity.ERROR,
                    p.position_id,
                    symbol=p.symbol,
                    venue_value=p.realized_pnl,
                    other_value=pnl,
                )
            )
    return out


# ------------------------------------------------------------------ journal


def _score(p: Position, e: JournalEntry, tol: Tolerances) -> float | None:
    """Lower is better; ``None`` = not a candidate."""
    if canonical(e.symbol) != p.symbol or e.side.lower() != p.side:
        return None
    t_open = abs(p.opened_ms - e.opened_ms)
    if t_open > tol.time_ms:
        return None
    score = t_open / tol.time_ms
    if p.status == "closed" and e.closed_ms is not None:
        t_close = abs(p.updated_ms - e.closed_ms)
        if t_close > tol.time_ms:
            return None
        score += t_close / tol.time_ms
    q = _rel(p.qty, e.qty)
    if q > tol.qty_rel:
        return None
    pr = _rel(p.entry_price, e.entry_price)
    if pr > tol.price_rel:
        return None
    return score + float(q / tol.qty_rel) + float(pr / tol.price_rel)


def match(
    positions: Iterable[Position],
    entries: Iterable[JournalEntry],
    tol: Tolerances = DEFAULT_TOLERANCES,
) -> MatchResult:
    ps = list(positions)
    es = list(entries)
    edges = sorted(
        (s, i, j)
        for i, p in enumerate(ps)
        for j, e in enumerate(es)
        if (s := _score(p, e, tol)) is not None
    )
    by_p: dict[int, list[tuple[float, int]]] = defaultdict(list)
    by_e: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for s, i, j in edges:
        by_p[i].append((s, j))
        by_e[j].append((s, i))

    def clear_winner(cands: list[tuple[float, int]], score: float, taken: set[int]) -> bool:
        rest = [s for s, k in cands if k not in taken and s != score]
        return not rest or min(rest) - score >= tol.ambiguity_margin

    result = MatchResult()
    used_p: set[int] = set()
    used_e: set[int] = set()
    ambiguous_p: set[int] = set()
    ambiguous_e: set[int] = set()
    for s, i, j in edges:
        if i in used_p or j in used_e or i in ambiguous_p or j in ambiguous_e:
            continue
        ties_p = [k for sc, k in by_p[i] if sc == s and k not in used_e]
        ties_e = [k for sc, k in by_e[j] if sc == s and k not in used_p]
        if (
            len(ties_p) > 1
            or len(ties_e) > 1
            or not clear_winner(by_p[i], s, used_e)
            or not clear_winner(by_e[j], s, used_p)
        ):
            # Everyone this close to the best pairing is in doubt, on both sides.
            ambiguous_p |= {i} | {
                k for sc, k in by_e[j] if k not in used_p and sc - s < tol.ambiguity_margin
            }
            ambiguous_e |= {j} | {
                k for sc, k in by_p[i] if k not in used_e and sc - s < tol.ambiguity_margin
            }
            continue
        used_p.add(i)
        used_e.add(j)
        result.pairs.append((ps[i], es[j]))
    for i in sorted(ambiguous_p - used_p):
        result.findings.append(
            Finding(Code.AMBIGUOUS_MATCH, Severity.WARNING, ps[i].position_id, symbol=ps[i].symbol)
        )
    for j in sorted(ambiguous_e - used_e):
        result.findings.append(
            Finding(
                Code.AMBIGUOUS_MATCH,
                Severity.WARNING,
                entry_id=es[j].entry_id,
                symbol=canonical(es[j].symbol),
            )
        )
    for i, p in enumerate(ps):
        if i not in used_p and i not in ambiguous_p:
            result.findings.append(
                Finding(Code.MISSING_IN_JOURNAL, Severity.WARNING, p.position_id, symbol=p.symbol)
            )
    for j, e in enumerate(es):
        if j not in used_e and j not in ambiguous_e:
            result.findings.append(
                Finding(
                    Code.MISSING_AT_VENUE,
                    Severity.WARNING,
                    entry_id=e.entry_id,
                    symbol=canonical(e.symbol),
                )
            )
    return result


def _venue_pnl(p: Position, basis: PnlBasis) -> Decimal:
    if basis is PnlBasis.GROSS:
        return p.realized_pnl
    if basis is PnlBasis.NET_OF_FEES:
        return p.realized_pnl - p.fee
    return p.net


def compare(p: Position, e: JournalEntry, tol: Tolerances = DEFAULT_TOLERANCES) -> list[Finding]:
    """The amounts of one matched pair, on aligned definitions."""
    out: list[Finding] = []

    def add(code: Code, venue_value: Decimal, other: Decimal) -> None:
        out.append(
            Finding(code, Severity.ERROR, p.position_id, e.entry_id, p.symbol, venue_value, other)
        )

    if (p.status == "closed") != (e.closed_ms is not None):
        out.append(
            Finding(Code.STATUS_MISMATCH, Severity.WARNING, p.position_id, e.entry_id, p.symbol)
        )
    if (
        p.close_price is not None
        and e.exit_price is not None
        and _rel(p.close_price, e.exit_price) > tol.price_rel
    ):
        add(Code.PRICE_MISMATCH, p.close_price, e.exit_price)
    if e.fee is not None and _differs(p.fee, e.fee, tol):
        add(Code.FEE_MISMATCH, p.fee, e.fee)
    if e.funding is not None and _differs(p.funding, e.funding, tol):
        add(Code.FUNDING_MISMATCH, p.funding, e.funding)
    if e.pnl is not None and p.status == "closed":
        venue = _venue_pnl(p, e.pnl_basis)
        if _differs(venue, e.pnl, tol):
            add(Code.PNL_MISMATCH, venue, e.pnl)
    return out


def reconcile_journal(
    positions: Iterable[Position],
    entries: Iterable[JournalEntry],
    tol: Tolerances = DEFAULT_TOLERANCES,
) -> MatchResult:
    """``match`` plus ``compare`` for every pair."""
    result = match(positions, entries, tol)
    for p, e in result.pairs:
        result.findings.extend(compare(p, e, tol))
    return result


# ------------------------------------------------------------------ balance


def check_balance(
    before: Balance,
    after: Balance,
    positions_before: dict[str, Position],
    positions_after: dict[str, Position],
    tol: Tolerances = DEFAULT_TOLERANCES,
) -> list[Finding]:
    """Δ wallet must equal Δ(realized − fee + funding) summed over all positions."""
    if before.margin_coin != after.margin_coin:
        raise ValueError("balances of different margin coins")
    explained = Decimal(0)
    for pid, now in positions_after.items():
        then = positions_before.get(pid)
        explained += now.net - (then.net if then is not None else Decimal(0))
    moved = after.wallet - before.wallet
    if _differs(moved, explained, tol):
        return [
            Finding(
                Code.UNEXPLAINED_BALANCE_CHANGE,
                Severity.WARNING,
                symbol=after.margin_coin,
                venue_value=moved,
                other_value=explained,
            )
        ]
    return []


# ------------------------------------------------------------------ exports


def compare_twin(
    api: Position, export: Position, tol: Tolerances = DEFAULT_TOLERANCES
) -> list[Finding]:
    """The API's copy of a position against the copy an export without ids gave."""
    out: list[Finding] = []

    def add(name: str, a: Decimal, b: Decimal) -> None:
        out.append(
            Finding(
                Code.EXPORT_DIFFERS_FROM_API,
                Severity.ERROR,
                api.position_id,
                symbol=api.symbol,
                venue_value=a,
                other_value=b,
                field=name,
            )
        )

    if _rel(api.qty, export.qty) > tol.qty_rel:
        add("qty", api.qty, export.qty)
    if _rel(api.entry_price, export.entry_price) > tol.price_rel:
        add("entry_price", api.entry_price, export.entry_price)
    if (
        api.close_price is not None
        and export.close_price is not None
        and _rel(api.close_price, export.close_price) > tol.price_rel
    ):
        add("close_price", api.close_price, export.close_price)
    for name in ("fee", "funding", "realized_pnl"):
        a, b = getattr(api, name), getattr(export, name)
        if _differs(a, b, tol):
            add("realized" if name == "realized_pnl" else name, a, b)
    return out


DAY_MS: Final = 86_400_000


def day_start_ms(day: str) -> int:
    """``"2026-10-11"`` → that UTC midnight in epoch ms."""
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * 1000


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _covered(coverage: list[tuple[int, int]], lo: int, hi: int) -> bool:
    return any(a <= lo and hi <= b for a, b in coverage)


def check_fill_totals(
    totals: Iterable[FillTotal],
    fills: Iterable[Fill],
    fill_coverage: list[tuple[int, int]],
    positions: Iterable[Position],
    tol: Tolerances = DEFAULT_TOLERANCES,
) -> list[Finding]:
    """Daily fill totals (an export without ids) against what the archive knows.

    Only ``complete`` days count. A day the archive's own fills cover
    completely is compared per symbol on fees and realized P/L. The other
    days are grouped into runs of consecutive days and compared per symbol
    with the realized P/L of the positions closed in the run — fees are not:
    a position's fee includes its opening, which may lie outside the run.
    """
    complete = [t for t in totals if t.complete]
    if not complete:
        return []
    by_day: dict[tuple[str, str], list[Fill]] = defaultdict(list)
    for f in fills:
        by_day[(utc_day(f.ts_ms), f.symbol)].append(f)
    out: list[Finding] = []
    days = sorted({t.day for t in complete})
    uncovered = [
        d for d in days if not _covered(fill_coverage, day_start_ms(d), day_start_ms(d) + DAY_MS)
    ]
    for t in complete:
        lo = day_start_ms(t.day)
        if t.day in uncovered:
            continue
        own = by_day.get((t.day, t.symbol), [])
        fee = sum((f.fee for f in own), Decimal(0))
        pnl = sum((f.realized_pnl for f in own), Decimal(0))
        for name, a, b in (("fee", fee, t.fee), ("realized", pnl, t.realized_pnl)):
            if _differs(a, b, tol):
                out.append(
                    Finding(
                        Code.FILL_TOTALS_MISMATCH,
                        Severity.ERROR,
                        symbol=t.symbol,
                        venue_value=a,
                        other_value=b,
                        field=name,
                        period=(lo, lo + DAY_MS),
                    )
                )
    # runs of consecutive uncovered days
    runs: list[tuple[int, int]] = []
    for d in uncovered:
        lo = day_start_ms(d)
        if runs and runs[-1][1] == lo:
            runs[-1] = (runs[-1][0], lo + DAY_MS)
        else:
            runs.append((lo, lo + DAY_MS))
    closed = [p for p in positions if p.status == "closed"]
    for lo, hi in runs:
        in_run = [t for t in complete if lo <= day_start_ms(t.day) < hi]
        symbols = {t.symbol for t in in_run} | {p.symbol for p in closed if lo <= p.updated_ms < hi}
        for sym in sorted(symbols):
            export = sum((t.realized_pnl for t in in_run if t.symbol == sym), Decimal(0))
            venue = sum(
                (p.realized_pnl for p in closed if p.symbol == sym and lo <= p.updated_ms < hi),
                Decimal(0),
            )
            if _differs(venue, export, tol):
                out.append(
                    Finding(
                        Code.FILL_TOTALS_MISMATCH,
                        Severity.WARNING,
                        symbol=sym,
                        venue_value=venue,
                        other_value=export,
                        field="realized",
                        period=(lo, hi),
                    )
                )
    return out


__all__ = [
    "DEFAULT_TOLERANCES",
    "Code",
    "Finding",
    "JournalEntry",
    "MatchResult",
    "PnlBasis",
    "Severity",
    "Tolerances",
    "check_balance",
    "check_fill_totals",
    "check_internal",
    "compare",
    "compare_twin",
    "day_start_ms",
    "utc_day",
    "match",
    "reconcile_journal",
]
