"""CSV import: the venue's own history exports and an external journal's export.

The venue's API serves only a limited look-back (order history: 90 days); what
is older can only come from the venue's own CSV export. An external trading
journal is imported the same way, as a CONTROL source for reconciliation.

Nothing here guesses silently. Every assumption a file forces is either
declared by the person importing it or reported:

- **Columns** are found by header aliases (case, spaces and punctuation
  ignored); two headers for one field refuse the file (``ambiguous_column``)
  unless ``mapping`` names the right one. The real export formats are not
  known yet, so the aliases are deliberately broad and ``mapping`` overrides
  them per field.
- **Numbers** are exact ``Decimal``. The decimal mark follows the delimiter
  (``;`` → comma, else point) unless declared; a thousands separator is only
  accepted in strict three-digit groups, so ``12,5`` in a point file is
  rejected instead of read as 125.
- **Times** are epoch seconds/milliseconds or ISO 8601 with an offset; a time
  without a zone needs a declared ``tz`` (else the file is refused,
  ``timezone_required``). A local time that does not exist (spring forward)
  is rejected; one that exists twice (fall back) is kept with a warning.
  Times before 2017 or in the future, and a close before the open, are
  rejected.
- **Fees** are stored as a positive cost. A file that shows every fee as a
  negative debit is flipped and the flip reported; mixed signs are kept and
  reported. A fee in another currency than the margin coin is rejected.
- **Funding** is signed, negative = paid (the venue's convention); a file
  with the opposite convention is declared with ``funding_sign``. A file
  without a funding column says so; its funding stays what the archive
  already knows (zero for a new record). Any optional field a file leaves
  empty keeps the stored value instead of overwriting it, and a time the
  file gives in whole seconds does not overwrite the same second's stored
  milliseconds.
- **P/L definitions.** The archive stores the venue's realized P/L, which
  EXCLUDES fees and funding. A file's P/L column is converted from its basis
  (``gross``, ``net_of_fees``, ``net``). The basis is declared, or — for
  positions and journal entries, which carry prices — inferred from the rows
  when they clearly fit one basis; otherwise the file is refused
  (``pnl_basis_required``). A declared basis the rows contradict is kept
  and reported (``pnl_basis_doubtful``). A position export with net P/L
  but no funding column is refused: its gross P/L cannot be derived.
- **Duplicates.** A record is keyed by the venue's own id (required for venue
  exports). The same id twice in one file is skipped when identical and
  rejected when different. A record the API already delivered is never
  overwritten by a CSV row — the API is the primary source; a differing row
  is reported (``conflicts_with_api``). Re-importing the same file changes
  nothing.

Layouts. ``standard`` is the shape above: one row per record with the
venue's id. ``web_export`` is what the venue's web interface exports: no ids
at all, symbol, side and margin mode in one column (``BTCUSDT Long·CROSS``),
sizes with their unit (``0.5 BTC``), and the P/L twice — gross and net.

- Its **positions** get a derived id (symbol, side, opening second; see
  ``models.derived_position_id``); a position the API already delivered with
  the same identity is matched to that one instead. The gross P/L is stored;
  the stated net is checked against gross − fee + funding, and a row that
  fails keeps its stated and derived value per position
  (``pnl_components_mismatch``) for the reconciliation to show.
- Its **fills** cannot be told apart (two fills in the same second look
  alike) nor assigned to a position, so they are stored only as daily totals
  per symbol (``FillTotal``); identical rows are kept and counted
  (``identical_rows``), and the file never counts as fill coverage.

Safety: the file is only read; size, rows, columns and cell length are
capped; text fields accept a narrow character set (no spreadsheet formulas);
issues carry line, code and field name — never a cell's content. A dry run
computes the same report inside a transaction that is rolled back.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.live_history.archive import LiveArchive, Outcome, RunStatus
from jarvis.live_history.models import (
    Entity,
    Fill,
    FillTotal,
    Position,
    Record,
    content_hash,
    decimal_text,
    derived_position_id,
    is_derived,
)
from jarvis.live_history.reconcile import Code, JournalEntry, PnlBasis, Severity, utc_day
from jarvis.live_history.symbols import canonical, split
from jarvis.live_history.sync import Stream

MAX_FILE_BYTES: Final = 20 * 1024 * 1024
MAX_ROWS: Final = 200_000
MAX_COLUMNS: Final = 64
MAX_CELL: Final = 256
#: Issues stored per run; the rest are only counted.
MAX_STORED_ISSUES: Final = 1000
EARLIEST_MS: Final = 1_483_228_800_000  # 2017-01-01 UTC
FUTURE_SLACK_MS: Final = 5 * 60_000
#: A share of the evaluable rows that must fit one P/L basis to infer it.
INFER_SHARE: Final = 0.8


class ImportKind(StrEnum):
    POSITIONS = "positions"  # the venue's position-history export
    FILLS = "fills"  # the venue's trade-history export
    JOURNAL = "journal"  # an external journal's export


class Layout(StrEnum):
    STANDARD = "standard"  # one row per record, with the venue's id
    WEB_EXPORT = "web_export"  # the venue's web export: no ids, combined contract column


class IssueCode(StrEnum):
    # the file is refused
    LAYOUT_UNSUPPORTED = "layout_unsupported"
    FILE_TOO_LARGE = "file_too_large"
    NOT_TEXT = "not_text"
    EMPTY_FILE = "empty_file"
    TOO_MANY_ROWS = "too_many_rows"
    TOO_MANY_COLUMNS = "too_many_columns"
    MISSING_COLUMN = "missing_column"
    AMBIGUOUS_COLUMN = "ambiguous_column"
    UNKNOWN_MAPPING = "unknown_mapping"
    TIMEZONE_REQUIRED = "timezone_required"
    BAD_TIMEZONE = "bad_timezone"
    PNL_BASIS_REQUIRED = "pnl_basis_required"
    NET_PNL_WITHOUT_FUNDING = "net_pnl_without_funding"
    # a row is rejected
    WRONG_COLUMN_COUNT = "wrong_column_count"
    CELL_TOO_LONG = "cell_too_long"
    MISSING_VALUE = "missing_value"
    BAD_NUMBER = "bad_number"
    NOT_POSITIVE = "not_positive"
    BAD_TEXT = "bad_text"
    BAD_CHOICE = "bad_choice"
    BAD_TIMESTAMP = "bad_timestamp"
    NONEXISTENT_LOCAL_TIME = "nonexistent_local_time"
    IMPLAUSIBLE_TIME = "implausible_time"
    FUTURE_TIME = "future_time"
    CLOSED_BEFORE_OPENED = "closed_before_opened"
    FOREIGN_FEE_CURRENCY = "foreign_fee_currency"
    UNIT_MISMATCH = "unit_mismatch"
    CONFLICTING_DUPLICATE = "conflicting_duplicate"
    CONFLICTS_WITH_API = "conflicts_with_api"
    # kept, but worth knowing
    AMBIGUOUS_LOCAL_TIME = "ambiguous_local_time"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    ENCODING_FALLBACK = "encoding_fallback"
    FEE_SIGN_FLIPPED = "fee_sign_flipped"
    MIXED_FEE_SIGNS = "mixed_fee_signs"
    NO_FUNDING_COLUMN = "no_funding_column"
    PNL_BASIS_INFERRED = "pnl_basis_inferred"
    PNL_BASIS_DOUBTFUL = "pnl_basis_doubtful"
    ALREADY_IMPORTED = "already_imported"
    PNL_COMPONENTS_MISMATCH = "pnl_components_mismatch"
    IDENTICAL_ROWS = "identical_rows"
    MATCHED_BY_IDENTITY = "matched_by_identity"
    KEPT_AS_TOTALS = "kept_as_totals"
    COVERAGE_NOT_RECORDED = "coverage_not_recorded"
    ISSUES_TRUNCATED = "issues_truncated"


@dataclass(frozen=True)
class Issue:
    code: IssueCode
    severity: Severity
    line: int | None = None  # 1 = the header line; None = the whole file
    field: str | None = None


class ImportRefused(Exception):
    """The file cannot be imported at all; nothing was written but the failed run."""

    def __init__(self, code: IssueCode, field: str | None = None) -> None:
        super().__init__(code.value if field is None else f"{code.value}: {field}")
        self.code = code
        self.field = field


class _RowError(Exception):
    def __init__(self, code: IssueCode, field: str | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.field = field


class _NeedTimezone(Exception):
    pass


# ------------------------------------------------------------------ profiles


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


_ID: Final = ("id",)
_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "position_id": ("positionid", "position", "positionno"),
    "trade_id": ("tradeid", "fillid", "tradeno", "executionid"),
    "order_id": ("orderid", "orderno"),
    "entry_id": ("entryid", "tradeid", "journalid", *_ID),
    "symbol": ("symbol", "futures", "contract", "pair", "market", "instrument"),
    "side": ("side", "direction", "positionside"),
    "qty": ("maxqty", "qty", "size", "quantity", "positionsize", "volume", "filledqty"),
    "entry_price": ("entryprice", "avgopenprice", "openprice", "averageopenprice", "entry"),
    "close_price": ("closeprice", "avgcloseprice", "averagecloseprice", "exitprice", "exit"),
    "price": ("price", "fillprice", "tradeprice", "avgprice", "executionprice"),
    "fee": ("fee", "fees", "tradingfee", "tradingfees", "commission", "transactionfee"),
    "fee_coin": ("feecoin", "feecurrency", "feeasset"),
    "funding": ("funding", "fundingfee", "fundingfees", "fundingpnl"),
    "pnl": ("realizedpnl", "realisedpnl", "closedpnl", "pnl", "profit", "netpnl", "profitloss"),
    "opened": ("ctime", "opentime", "opened", "openedat", "openingtime", "opendate", "entrytime"),
    "closed": ("mtime", "closetime", "closed", "closedat", "closingtime", "closedate", "exittime"),
    "time": ("ctime", "time", "tradetime", "filltime", "executiontime", "date", "timestamp"),
    "role": ("roletype", "role", "liquidity", "makertaker"),
    "leverage": ("leverage",),
    "margin_mode": ("marginmode",),
}

#: Header aliases of the ``web_export`` layout (they replace the standard ones).
_WEB_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "contract": ("futures", "contract"),
    "symbol": ("futures", "symbol"),
    "action": ("side",),
    "qty": ("closedamount", "transactionamount"),
    "entry_price": ("averageentryprice",),
    "close_price": ("averageclosingprice",),
    "price": ("averageprice",),
    "fee": ("positionfee", "fee"),
    "fee_coin": ("coin",),
    "funding": ("fundingfees",),
    "pnl": ("positionpnl", "realizedpl"),
    "pnl_net": ("realizedpnl", "netprofits"),
    "opened": ("openingtime",),
    "closed": ("closedtime",),
    "time": ("date",),
}

_FIELDS: Final[dict[ImportKind, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    # (required, optional)
    ImportKind.POSITIONS: (
        (
            "position_id",
            "symbol",
            "side",
            "qty",
            "entry_price",
            "fee",
            "pnl",
            "opened",
            "closed",
        ),
        ("close_price", "funding", "fee_coin", "leverage", "margin_mode"),
    ),
    ImportKind.FILLS: (
        ("trade_id", "order_id", "symbol", "qty", "price", "fee", "time"),
        ("position_id", "side", "pnl", "role", "fee_coin"),
    ),
    ImportKind.JOURNAL: (
        ("symbol", "side", "qty", "entry_price", "opened"),
        ("entry_id", "close_price", "closed", "fee", "funding", "pnl", "fee_coin"),
    ),
}

_WEB_FIELDS: Final[dict[ImportKind, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    ImportKind.POSITIONS: (
        (
            "contract",
            "qty",
            "entry_price",
            "close_price",
            "fee",
            "funding",
            "pnl",
            "pnl_net",
            "opened",
            "closed",
        ),
        ("fee_coin",),
    ),
    ImportKind.FILLS: (
        ("symbol", "action", "qty", "price", "fee", "pnl", "time"),
        ("pnl_net", "fee_coin"),
    ),
}


def _fields(kind: ImportKind, layout: Layout) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if layout is Layout.STANDARD:
        return _FIELDS[kind]
    if kind not in _WEB_FIELDS:
        raise ImportRefused(IssueCode.LAYOUT_UNSUPPORTED, kind.value)
    return _WEB_FIELDS[kind]


_TEXT_OK: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/\-#]*$")
_POSITION_SIDES: Final = {"long": "long", "buy": "long", "short": "short", "sell": "short"}
_FILL_SIDES: Final = {
    "buy": "buy",
    "sell": "sell",
    "openlong": "buy",
    "closeshort": "buy",
    "openshort": "sell",
    "closelong": "sell",
}
_ROLES: Final = {"maker": "maker", "taker": "taker", "m": "maker", "t": "taker"}
_ACTIONS: Final = {
    "openlong": "open_long",
    "openshort": "open_short",
    "closelong": "close_long",
    "closeshort": "close_short",
}
#: ``BTCUSDT Long·ISOLATION`` → symbol, side, margin mode (the separator varies).
_CONTRACT: Final = re.compile(
    r"^([A-Za-z0-9]+(?:[/_-][A-Za-z0-9]+)?)\s+(long|short)(?:\s*[^A-Za-z0-9\s]?\s*"
    r"(isolation|isolated|cross))?$",
    re.I,
)
_MARGIN_MODES: Final = {"isolation": "ISOLATION", "isolated": "ISOLATION", "cross": "CROSS"}
_SIZED: Final = re.compile(r"^(.*?\d)\s*([A-Za-z]{2,12})$")


def resolve_columns(
    headers: list[str],
    kind: ImportKind,
    mapping: Mapping[str, str] | None = None,
    layout: Layout = Layout.STANDARD,
) -> dict[str, int]:
    """Field → column index. Refuses missing required and doubly matched fields."""
    required, optional = _fields(kind, layout)
    table = {**_ALIASES, **_WEB_ALIASES} if layout is Layout.WEB_EXPORT else _ALIASES
    wanted = (*required, *optional)
    normalized = [_norm(h) for h in headers]
    mapping = dict(mapping or {})
    unknown = set(mapping) - set(wanted)
    if unknown:
        raise ImportRefused(IssueCode.UNKNOWN_MAPPING, sorted(unknown)[0])
    out: dict[str, int] = {}
    taken: set[int] = set()
    for name in wanted:
        if name in mapping:
            target = _norm(mapping[name])
            hits = [i for i, h in enumerate(normalized) if h == target]
        else:
            aliases = table[name]
            hits = [i for i, h in enumerate(normalized) if h in aliases and i not in taken]
            # the most specific alias wins when one header is an exact field name
            if len(hits) > 1:
                ranked = sorted(hits, key=lambda i: aliases.index(normalized[i]))
                best = aliases.index(normalized[ranked[0]])
                if aliases.index(normalized[ranked[1]]) == best:
                    raise ImportRefused(IssueCode.AMBIGUOUS_COLUMN, name)
                if name in ("pnl", "qty"):
                    # two P/L or size columns mean two definitions: never pick one silently
                    raise ImportRefused(IssueCode.AMBIGUOUS_COLUMN, name)
                hits = ranked[:1]
        if not hits:
            if name in required:
                raise ImportRefused(IssueCode.MISSING_COLUMN, name)
            continue
        if len(hits) > 1:
            raise ImportRefused(IssueCode.AMBIGUOUS_COLUMN, name)
        out[name] = hits[0]
        taken.add(hits[0])
    return out


# ------------------------------------------------------------- cell parsing

_SPACES: Final = re.compile(r"[\s   ']")
_CURRENCY: Final = re.compile(r"(\s*(USDT|USDC|USD|\$))$", re.I)
_POINT_GROUPED: Final = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_COMMA_GROUPED: Final = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+(,\d+)?$")
_PLAIN_POINT: Final = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")
_PLAIN_COMMA: Final = re.compile(r"^[+-]?\d+(,\d+)?$")


def parse_decimal(raw: str, *, decimal_comma: bool) -> Decimal:
    """``"1,234.50"`` / ``"1.234,50"`` / ``"(3.10)"`` / ``"−0.2 USDT"`` → exact Decimal."""
    s = _CURRENCY.sub("", raw.strip()).replace("−", "-")
    s = _SPACES.sub("", s)
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    if decimal_comma:
        if _COMMA_GROUPED.match(s):
            s = s.replace(".", "")
        elif not _PLAIN_COMMA.match(s):
            raise _RowError(IssueCode.BAD_NUMBER)
        s = s.replace(",", ".")
    else:
        if _POINT_GROUPED.match(s):
            s = s.replace(",", "")
        elif not _PLAIN_POINT.match(s):
            raise _RowError(IssueCode.BAD_NUMBER)
    try:
        value = Decimal(s)
    except InvalidOperation:  # the patterns above should make this unreachable
        raise _RowError(IssueCode.BAD_NUMBER) from None
    return -value if negative else value


_DOTTED: Final = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})[ T](\d{2}):(\d{2})(?::(\d{2}))?$")


def parse_time(raw: str, tz: ZoneInfo | None) -> tuple[int, bool]:
    """``(epoch ms UTC, the local time was ambiguous)``.

    Raises ``_NeedTimezone`` for a zoneless time when no ``tz`` is declared.
    """
    s = raw.strip()
    if s.isdigit():
        if len(s) == 13:
            return int(s), False
        if len(s) == 10:
            return int(s) * 1000, False
        raise _RowError(IssueCode.BAD_TIMESTAMP)
    m = _DOTTED.match(s)
    try:
        if m:
            day, month, year, hh, mm, ss = m.groups()
            dt = datetime(int(year), int(month), int(day), int(hh), int(mm), int(ss or 0))
        else:
            iso = s.replace("/", "-")
            if iso.endswith(("Z", "z")):
                iso = iso[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso)
    except ValueError:  # re-raised as a row issue without the cell's content
        raise _RowError(IssueCode.BAD_TIMESTAMP) from None
    if dt.tzinfo is not None:
        return _epoch_ms(dt), False
    if tz is None:
        raise _NeedTimezone
    early = dt.replace(tzinfo=tz, fold=0)
    late = dt.replace(tzinfo=tz, fold=1)
    # A wall time in the spring-forward gap does not survive a round trip.
    back = early.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if back != dt:
        raise _RowError(IssueCode.NONEXISTENT_LOCAL_TIME)
    ambiguous = early.utcoffset() != late.utcoffset()
    return _epoch_ms(early), ambiguous


_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def _epoch_ms(dt: datetime) -> int:
    """Exact milliseconds (no float rounding of ``timestamp()``)."""
    return (dt - _EPOCH) // timedelta(milliseconds=1)


# ------------------------------------------------------------------- report


@dataclass
class ImportReport:
    kind: ImportKind
    dry_run: bool
    status: RunStatus
    file_sha256: str
    run_id: int | None = None
    rows: int = 0
    counts: Counter[Outcome] = field(default_factory=Counter)
    rejected: int = 0
    duplicates_in_file: int = 0
    conflicts: int = 0
    issues: list[Issue] = field(default_factory=list)
    issue_total: int = 0
    pnl_basis: PnlBasis | None = None
    pnl_basis_source: Literal["declared", "inferred", "layout"] | None = None
    fee_sign_flipped: bool = False
    delimiter: str = ","
    decimal_comma: bool = False
    encoding: str = "utf-8"
    span_ms: tuple[int, int] | None = None
    refused: IssueCode | None = None
    layout: Layout = Layout.STANDARD
    #: rows of an export without ids that matched a position the API delivered
    matched_by_identity: int = 0

    def add(self, issue: Issue) -> None:
        self.issue_total += 1
        if len(self.issues) < MAX_STORED_ISSUES:
            self.issues.append(issue)

    def by_code(self) -> dict[str, int]:
        return dict(Counter(i.code.value for i in self.issues))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "layout": self.layout.value,
            "dry_run": self.dry_run,
            "status": self.status.value,
            "refused": self.refused.value if self.refused else None,
            "file_sha256": self.file_sha256,
            "rows": self.rows,
            "changes": {o.value: n for o, n in self.counts.items()},
            "rejected": self.rejected,
            "duplicates_in_file": self.duplicates_in_file,
            "conflicts_with_api": self.conflicts,
            "pnl_basis": self.pnl_basis.value if self.pnl_basis else None,
            "pnl_basis_source": self.pnl_basis_source,
            "fee_sign_flipped": self.fee_sign_flipped,
            "matched_by_identity": self.matched_by_identity,
            "delimiter": self.delimiter,
            "decimal_comma": self.decimal_comma,
            "encoding": self.encoding,
            "issues": self.by_code(),
            "issue_total": self.issue_total,
        }


@dataclass(frozen=True)
class ImportOptions:
    kind: ImportKind
    venue: str = "primary"
    account: str = "main"
    #: Which external journal the entries belong to (``JOURNAL`` only).
    journal: str = "control"
    margin_coin: str = "USDT"
    mapping: Mapping[str, str] | None = None
    tz: str | None = None
    #: ``None`` = from the delimiter (``;`` → comma).
    decimal_comma: bool | None = None
    fee_sign: Literal["auto", "cost_positive", "cost_negative"] = "auto"
    funding_sign: Literal["received_positive", "paid_positive"] = "received_positive"
    pnl_basis: PnlBasis | None = None
    #: A time span the file is known to cover COMPLETELY (recorded as coverage).
    covers: tuple[int, int] | None = None
    layout: Layout = Layout.STANDARD


# --------------------------------------------------------------------- read


def _read_text(path: Path, report: ImportReport) -> str:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ImportRefused(IssueCode.FILE_TOO_LARGE)
    blob = path.read_bytes()
    report.file_sha256 = hashlib.sha256(blob).hexdigest()
    if b"\x00" in blob:
        raise ImportRefused(IssueCode.NOT_TEXT)
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:  # read as cp1252 and reported (encoding_fallback)
        # Spreadsheet programs on Windows still save CSV as cp1252.
        text = blob.decode("cp1252", errors="strict")
        report.encoding = "cp1252"
        report.add(Issue(IssueCode.ENCODING_FALLBACK, Severity.WARNING))
    if not text.strip():
        raise ImportRefused(IssueCode.EMPTY_FILE)
    return text


def _delimiter(text: str) -> str:
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:  # no clear delimiter: a comma; a wrong guess shows as missing columns
        return ","


# -------------------------------------------------------------------- rows


@dataclass
class _Row:
    line: int
    values: dict[str, Any]
    ambiguous_time: bool = False


class _Parser:
    def __init__(
        self,
        opts: ImportOptions,
        columns: dict[str, int],
        width: int,
        decimal_comma: bool,
        tz: ZoneInfo | None,
        now_ms: int,
    ) -> None:
        self.opts = opts
        self.columns = columns
        self.width = width
        self.decimal_comma = decimal_comma
        self.tz = tz
        self.now_ms = now_ms

    def cell(self, cells: list[str], name: str) -> str:
        idx = self.columns.get(name)
        return "" if idx is None else cells[idx].strip()

    def text(self, cells: list[str], name: str, *, required: bool) -> str | None:
        raw = self.cell(cells, name)
        if not raw:
            if required:
                raise _RowError(IssueCode.MISSING_VALUE, name)
            return None
        if not _TEXT_OK.match(raw):
            raise _RowError(IssueCode.BAD_TEXT, name)
        return raw

    def number(self, cells: list[str], name: str, *, required: bool) -> Decimal | None:
        raw = self.cell(cells, name)
        if not raw or raw in ("-", "--", "—"):
            if required:
                raise _RowError(IssueCode.MISSING_VALUE, name)
            return None
        try:
            return parse_decimal(raw, decimal_comma=self.decimal_comma)
        except _RowError as exc:
            raise _RowError(exc.code, name) from None

    def positive(self, cells: list[str], name: str) -> Decimal:
        value = self.number(cells, name, required=True)
        assert value is not None
        if value <= 0:
            raise _RowError(IssueCode.NOT_POSITIVE, name)
        return value

    def time(self, cells: list[str], name: str, *, required: bool) -> tuple[int | None, bool]:
        raw = self.cell(cells, name)
        if not raw:
            if required:
                raise _RowError(IssueCode.MISSING_VALUE, name)
            return None, False
        try:
            ms, ambiguous = parse_time(raw, self.tz)
        except _RowError as exc:
            raise _RowError(exc.code, name) from None
        if ms < EARLIEST_MS:
            raise _RowError(IssueCode.IMPLAUSIBLE_TIME, name)
        if ms > self.now_ms + FUTURE_SLACK_MS:
            raise _RowError(IssueCode.FUTURE_TIME, name)
        return ms, ambiguous

    def choice(
        self, cells: list[str], name: str, table: Mapping[str, str], *, required: bool
    ) -> str | None:
        raw = self.cell(cells, name)
        if not raw:
            if required:
                raise _RowError(IssueCode.MISSING_VALUE, name)
            return None
        value = table.get(_norm(raw))
        if value is None:
            raise _RowError(IssueCode.BAD_CHOICE, name)
        return value

    def symbol(self, cells: list[str]) -> str:
        raw = self.text(cells, "symbol", required=True)
        assert raw is not None
        return canonical(raw)

    def fee_coin_ok(self, cells: list[str], symbol: str) -> None:
        coin = self.cell(cells, "fee_coin")
        if not coin:
            return
        parts = split(symbol)
        margin = parts[1] if parts else self.opts.margin_coin
        if coin.strip().upper() not in {margin, self.opts.margin_coin}:
            raise _RowError(IssueCode.FOREIGN_FEE_CURRENCY, "fee_coin")

    def contract(self, cells: list[str]) -> tuple[str, str, str | None]:
        """``BTCUSDT Long·ISOLATION`` → ``("BTCUSDT", "long", "ISOLATION")``."""
        raw = self.cell(cells, "contract")
        if not raw:
            raise _RowError(IssueCode.MISSING_VALUE, "contract")
        m = _CONTRACT.match(raw)
        if not m:
            raise _RowError(IssueCode.BAD_CHOICE, "contract")
        mode = _MARGIN_MODES[m.group(3).lower()] if m.group(3) else None
        return canonical(m.group(1)), m.group(2).lower(), mode

    def sized(self, cells: list[str], name: str, symbol: str) -> Decimal:
        """A positive size, optionally followed by its unit, which must be the base asset."""
        raw = self.cell(cells, name)
        m = _SIZED.match(raw)
        if m:
            parts = split(symbol)
            if parts is None or m.group(2).upper() != parts[0]:
                raise _RowError(IssueCode.UNIT_MISMATCH, name)
            raw = m.group(1)
        try:
            value = parse_decimal(raw, decimal_comma=self.decimal_comma) if raw else None
        except _RowError as exc:
            raise _RowError(exc.code, name) from None
        if value is None:
            raise _RowError(IssueCode.MISSING_VALUE, name)
        if value <= 0:
            raise _RowError(IssueCode.NOT_POSITIVE, name)
        return value

    def parse(self, line: int, cells: list[str]) -> _Row:
        if len(cells) != self.width:
            raise _RowError(IssueCode.WRONG_COLUMN_COUNT)
        if any(len(c) > MAX_CELL for c in cells):
            raise _RowError(IssueCode.CELL_TOO_LONG)
        if self.opts.layout is Layout.WEB_EXPORT:
            return self._parse_web(line, cells)
        kind = self.opts.kind
        v: dict[str, Any] = {}
        ambiguous = False
        v["symbol"] = self.symbol(cells)
        self.fee_coin_ok(cells, v["symbol"])
        if kind is ImportKind.FILLS:
            v["trade_id"] = self.text(cells, "trade_id", required=True)
            v["order_id"] = self.text(cells, "order_id", required=True)
            v["position_id"] = self.text(cells, "position_id", required=False)
            v["side"] = self.choice(cells, "side", _FILL_SIDES, required=False)
            v["qty"] = self.positive(cells, "qty")
            v["price"] = self.positive(cells, "price")
            v["fee"] = self.number(cells, "fee", required=True)
            v["pnl"] = self.number(cells, "pnl", required=False)
            v["role"] = self.choice(cells, "role", _ROLES, required=False)
            v["time"], ambiguous = self.time(cells, "time", required=True)
            return _Row(line, v, ambiguous)
        v["side"] = self.choice(cells, "side", _POSITION_SIDES, required=True)
        v["qty"] = self.positive(cells, "qty")
        v["entry_price"] = self.positive(cells, "entry_price")
        close = self.number(cells, "close_price", required=False)
        if close is not None and close <= 0:
            raise _RowError(IssueCode.NOT_POSITIVE, "close_price")
        v["close_price"] = close
        v["funding"] = self.number(cells, "funding", required=False)
        v["opened"], amb_open = self.time(cells, "opened", required=True)
        closing_required = kind is ImportKind.POSITIONS
        v["closed"], amb_close = self.time(cells, "closed", required=closing_required)
        ambiguous = amb_open or amb_close
        if v["closed"] is not None and v["closed"] < v["opened"]:
            raise _RowError(IssueCode.CLOSED_BEFORE_OPENED, "closed")
        if kind is ImportKind.POSITIONS:
            v["position_id"] = self.text(cells, "position_id", required=True)
            v["fee"] = self.number(cells, "fee", required=True)
            v["pnl"] = self.number(cells, "pnl", required=True)
            lev = self.number(cells, "leverage", required=False)
            v["leverage"] = int(lev) if lev is not None and lev == lev.to_integral_value() else None
            v["margin_mode"] = self.text(cells, "margin_mode", required=False)
        else:
            v["entry_id"] = self.text(cells, "entry_id", required=False)
            v["fee"] = self.number(cells, "fee", required=False)
            v["pnl"] = self.number(cells, "pnl", required=False)
        return _Row(line, v, ambiguous)

    def _parse_web(self, line: int, cells: list[str]) -> _Row:
        v: dict[str, Any] = {}
        if self.opts.kind is ImportKind.FILLS:
            v["symbol"] = self.symbol(cells)
            self.fee_coin_ok(cells, v["symbol"])
            action = self.choice(cells, "action", _ACTIONS, required=True)
            assert action is not None
            v["action"] = action
            v["qty"] = self.sized(cells, "qty", v["symbol"])
            v["price"] = self.positive(cells, "price")
            v["fee"] = self.number(cells, "fee", required=True)
            v["pnl"] = self.number(cells, "pnl", required=True)
            v["pnl_net"] = self.number(cells, "pnl_net", required=False)
            v["time"], ambiguous = self.time(cells, "time", required=True)
            return _Row(line, v, ambiguous)
        v["symbol"], v["side"], v["margin_mode"] = self.contract(cells)
        self.fee_coin_ok(cells, v["symbol"])
        v["qty"] = self.sized(cells, "qty", v["symbol"])
        v["entry_price"] = self.positive(cells, "entry_price")
        v["close_price"] = self.positive(cells, "close_price")
        v["fee"] = self.number(cells, "fee", required=True)
        v["funding"] = self.number(cells, "funding", required=True)
        v["pnl"] = self.number(cells, "pnl", required=True)
        v["pnl_net"] = self.number(cells, "pnl_net", required=True)
        v["opened"], amb_open = self.time(cells, "opened", required=True)
        v["closed"], amb_close = self.time(cells, "closed", required=True)
        if v["closed"] < v["opened"]:
            raise _RowError(IssueCode.CLOSED_BEFORE_OPENED, "closed")
        v["leverage"] = None
        v["position_id"] = derived_position_id(v["symbol"], v["side"], v["opened"])
        return _Row(line, v, amb_open or amb_close)


# --------------------------------------------------------- P/L basis, signs


def _gross_from_prices(v: dict[str, Any]) -> Decimal | None:
    if v.get("close_price") is None:
        return None
    sign = 1 if v["side"] == "long" else -1
    return (v["close_price"] - v["entry_price"]) * v["qty"] * sign


def _fits(pnl: Decimal, expected: Decimal) -> bool:
    return abs(pnl - expected) <= max(Decimal("0.02"), abs(expected) * Decimal("0.005"))


def infer_pnl_basis(rows: list[dict[str, Any]]) -> PnlBasis | None:
    """The basis the rows' P/L clearly follows, from their prices, fees and funding.

    Only rows where the candidates differ count; ``None`` when fewer than
    ``INFER_SHARE`` of them fit one basis, or when no row can tell them apart.
    """
    votes: Counter[PnlBasis] = Counter()
    evaluable = 0
    for v in rows:
        gross = _gross_from_prices(v)
        if gross is None or v.get("pnl") is None or v.get("fee") is None:
            continue
        fee = v["fee"]
        funding = v.get("funding") or Decimal(0)
        candidates = {
            PnlBasis.GROSS: gross,
            PnlBasis.NET_OF_FEES: gross - fee,
            PnlBasis.NET: gross - fee + funding,
        }
        fitting = [b for b, expected in candidates.items() if _fits(v["pnl"], expected)]
        if len(fitting) != 1:
            continue  # indistinguishable (no fee, no funding) or fits nothing
        evaluable += 1
        votes[fitting[0]] += 1
    if not evaluable:
        return None
    basis, n = votes.most_common(1)[0]
    return basis if n / evaluable >= INFER_SHARE else None


def to_gross(pnl: Decimal, basis: PnlBasis, fee: Decimal, funding: Decimal) -> Decimal:
    """A P/L on ``basis`` → the venue's realized P/L (excluding fees and funding)."""
    if basis is PnlBasis.GROSS:
        return pnl
    if basis is PnlBasis.NET_OF_FEES:
        return pnl + fee
    return pnl + fee - funding


# ------------------------------------------------------------------ records


#: Record fields a file may leave empty, by the row value they come from.
_POSITION_OPTIONAL: Final = {
    "liq_qty": "liq_qty",  # exports do not carry it
    "close_price": "close_price",
    "funding": "funding",
    "leverage": "leverage",
    "margin_mode": "margin_mode",
}
_FILL_OPTIONAL: Final = {
    "reduce_only": "reduce_only",  # exports do not carry it
    "client_id": "client_id",  # nor this
    "position_id": "position_id",
    "side": "side",
    "realized_pnl": "pnl",
    "role": "role",
}


def _position(v: dict[str, Any], o: ImportOptions, basis: PnlBasis) -> Position:
    funding = v["funding"] if v["funding"] is not None else Decimal(0)
    return Position(
        venue=o.venue,
        account=o.account,
        position_id=v["position_id"],
        symbol=v["symbol"],
        side=v["side"],
        status="closed",
        qty=v["qty"],
        entry_price=v["entry_price"],
        close_price=v["close_price"],
        fee=v["fee"],
        funding=funding,
        realized_pnl=to_gross(v["pnl"], basis, v["fee"], funding),
        unrealized_pnl=None,
        liq_qty=None,
        leverage=v["leverage"],
        margin_mode=v["margin_mode"],
        opened_ms=v["opened"],
        updated_ms=v["closed"],
    )


def _fill(v: dict[str, Any], o: ImportOptions, basis: PnlBasis | None) -> Fill:
    pnl = v["pnl"] if v["pnl"] is not None else Decimal(0)
    if basis is not None:
        pnl = to_gross(pnl, basis, v["fee"], Decimal(0))
    return Fill(
        venue=o.venue,
        account=o.account,
        trade_id=v["trade_id"],
        order_id=v["order_id"],
        position_id=v["position_id"],
        symbol=v["symbol"],
        side=v["side"],
        qty=v["qty"],
        price=v["price"],
        fee=v["fee"],
        realized_pnl=pnl,
        role=v["role"],
        reduce_only=False,
        client_id=None,
        ts_ms=v["time"],
    )


def _journal(v: dict[str, Any], basis: PnlBasis | None, key: str) -> JournalEntry:
    return JournalEntry(
        entry_id=key,
        symbol=v["symbol"],
        side=v["side"],
        qty=v["qty"],
        entry_price=v["entry_price"],
        opened_ms=v["opened"],
        closed_ms=v["closed"],
        exit_price=v["close_price"],
        fee=v["fee"],
        funding=v["funding"],
        pnl=v["pnl"],
        pnl_basis=basis or PnlBasis.NET,
    )


def journal_key(v: dict[str, Any]) -> str:
    """The journal's own id, or a stable digest of what identifies the trade."""
    if v.get("entry_id"):
        return str(v["entry_id"])
    ident = "|".join(str(v[k]) for k in ("symbol", "side", "opened", "qty", "entry_price"))
    return "h:" + hashlib.sha256(ident.encode()).hexdigest()[:20]


def _row_key(kind: ImportKind, v: dict[str, Any]) -> str:
    if kind is ImportKind.POSITIONS:
        return str(v["position_id"])
    if kind is ImportKind.FILLS:
        return str(v["trade_id"])
    return journal_key(v)


def _row_digest(v: dict[str, Any]) -> str:
    """Identity of a row's meaning: ``1.50`` and ``1.5`` are the same amount."""
    norm = {k: decimal_text(x) if isinstance(x, Decimal) else x for k, x in sorted(v.items())}
    return hashlib.sha256(json.dumps(norm, default=str).encode()).hexdigest()


# --------------------------------------------------------------------- main


def import_csv(
    archive: LiveArchive,
    path: Path,
    opts: ImportOptions,
    *,
    now_ms: Callable[[], int],
    dry_run: bool = False,
) -> ImportReport:
    """Import one CSV file into the archive (or, as a dry run, report what it would do)."""
    report = ImportReport(opts.kind, dry_run, RunStatus.RUNNING, file_sha256="")
    started = now_ms()
    source = f"csv:{opts.kind.value}"
    try:
        rows = _prepare(path, opts, report, started)
    except ImportRefused as exc:
        report.status = RunStatus.FAILED
        report.refused = exc.code
        report.add(Issue(exc.code, Severity.ERROR, field=exc.field))
        if not dry_run:
            with archive.batch():
                run_id = archive.begin_run(
                    venue=opts.venue,
                    account=opts.account,
                    kind="import",
                    source=f"{source}:{report.file_sha256[:12]}",
                    now_ms=started,
                )
                archive.add_issues(run_id, [_issue_row(i) for i in report.issues])
                archive.finish_run(
                    run_id,
                    status=RunStatus.FAILED,
                    now_ms=now_ms(),
                    requests=0,
                    counts={},
                    rejected=0,
                    error_code=exc.code.value,
                    detail=json.dumps(report.as_dict(), sort_keys=True),
                )
            report.run_id = run_id
        return report

    source = f"{source}:{report.file_sha256[:12]}"
    if _already_imported(archive, opts, source):
        report.add(Issue(IssueCode.ALREADY_IMPORTED, Severity.INFO))
    with archive.batch(commit=not dry_run):
        run_id = archive.begin_run(
            venue=opts.venue, account=opts.account, kind="import", source=source, now_ms=started
        )
        ts = now_ms()
        for item in rows:
            _store(archive, opts, item, run_id, ts, source, report)
        if report.matched_by_identity:
            report.add(Issue(IssueCode.MATCHED_BY_IDENTITY, Severity.INFO))
        clean = report.rejected == 0 and report.conflicts == 0
        report.status = RunStatus.OK if clean else RunStatus.PARTIAL
        if opts.covers is not None and clean and _keeps_records(opts):
            stream = Stream.POSITIONS if opts.kind is ImportKind.POSITIONS else Stream.FILLS
            archive.add_coverage(
                venue=opts.venue,
                account=opts.account,
                stream=stream,
                from_ms=opts.covers[0],
                to_ms=opts.covers[1],
                run_id=run_id,
            )
        if report.issue_total > len(report.issues):
            report.issues.append(Issue(IssueCode.ISSUES_TRUNCATED, Severity.INFO))
        archive.add_issues(run_id, [_issue_row(i) for i in report.issues])
        archive.finish_run(
            run_id,
            status=report.status,
            now_ms=now_ms(),
            requests=0,
            counts=dict(report.counts),
            rejected=report.rejected + report.conflicts,
            detail=json.dumps(report.as_dict(), sort_keys=True),
        )
        report.run_id = None if dry_run else run_id
    return report


def _keeps_records(opts: ImportOptions) -> bool:
    """False for a journal (its own table) and for fills kept only as totals."""
    if opts.kind is ImportKind.JOURNAL:
        return False
    return not (opts.kind is ImportKind.FILLS and opts.layout is Layout.WEB_EXPORT)


def _issue_row(i: Issue) -> tuple[int | None, str, str, str | None]:
    return (i.line, i.code.value, i.severity.value, i.field)


def _already_imported(archive: LiveArchive, opts: ImportOptions, source: str) -> bool:
    return any(
        r.source == source and r.status == RunStatus.OK
        for r in archive.runs(venue=opts.venue, account=opts.account, limit=10_000)
    )


def _prepare(path: Path, opts: ImportOptions, report: ImportReport, now: int) -> list[_Prepared]:
    text = _read_text(path, report)
    report.delimiter = _delimiter(text)
    report.decimal_comma = (
        opts.decimal_comma if opts.decimal_comma is not None else report.delimiter == ";"
    )
    tz: ZoneInfo | None = None
    if opts.tz:
        try:
            tz = ZoneInfo(opts.tz)
        except (ZoneInfoNotFoundError, ValueError):  # refused with its own code
            raise ImportRefused(IssueCode.BAD_TIMEZONE) from None
    reader = csv.reader(io.StringIO(text), delimiter=report.delimiter)
    try:
        headers = next(reader)
    except StopIteration:  # an empty file was refused above; a lone newline lands here
        raise ImportRefused(IssueCode.EMPTY_FILE) from None
    if len(headers) > MAX_COLUMNS:
        raise ImportRefused(IssueCode.TOO_MANY_COLUMNS)
    report.layout = opts.layout
    columns = resolve_columns(headers, opts.kind, opts.mapping, opts.layout)
    as_totals = not _keeps_records(opts) and opts.kind is ImportKind.FILLS
    parser = _Parser(opts, columns, len(headers), report.decimal_comma, tz, now)

    parsed: list[_Row] = []
    seen: dict[str, tuple[str, int]] = {}
    conflicted: set[str] = set()
    seen_rows: set[str] = set()
    for idx, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue  # blank line
        report.rows += 1
        if report.rows > MAX_ROWS:
            raise ImportRefused(IssueCode.TOO_MANY_ROWS)
        try:
            row = parser.parse(idx, cells)
        except _NeedTimezone:
            raise ImportRefused(IssueCode.TIMEZONE_REQUIRED) from None
        except _RowError as exc:
            report.rejected += 1
            report.add(Issue(exc.code, Severity.ERROR, idx, exc.field))
            continue
        digest = _row_digest(row.values)
        if as_totals:
            # No id: a repeated row may be a second fill in the same second. Kept, counted.
            if digest in seen_rows:
                report.add(Issue(IssueCode.IDENTICAL_ROWS, Severity.INFO, idx))
            seen_rows.add(digest)
            parsed.append(row)
            continue
        key = _row_key(opts.kind, row.values)
        if key in seen:
            if seen[key][0] == digest:
                report.duplicates_in_file += 1
                report.add(Issue(IssueCode.DUPLICATE_IN_FILE, Severity.INFO, idx))
            else:
                conflicted.add(key)
                report.rejected += 1
                report.add(Issue(IssueCode.CONFLICTING_DUPLICATE, Severity.ERROR, idx))
            continue
        seen[key] = (digest, idx)
        if row.ambiguous_time:
            report.add(Issue(IssueCode.AMBIGUOUS_LOCAL_TIME, Severity.WARNING, idx))
        parsed.append(row)
    if conflicted:
        # The first copy of a conflicting key is no more trustworthy than the second.
        keep: list[_Row] = []
        for row in parsed:
            if _row_key(opts.kind, row.values) in conflicted:
                report.rejected += 1
                report.add(Issue(IssueCode.CONFLICTING_DUPLICATE, Severity.ERROR, row.line))
            else:
                keep.append(row)
        parsed = keep

    _apply_fee_sign(parsed, opts, report)
    if "funding" not in columns and opts.kind is not ImportKind.FILLS:
        report.add(Issue(IssueCode.NO_FUNDING_COLUMN, Severity.WARNING, field="funding"))
    if opts.funding_sign == "paid_positive":
        for row in parsed:
            if row.values.get("funding") is not None:
                row.values["funding"] = -row.values["funding"]
    basis = _pnl_basis(parsed, opts, report, has_pnl="pnl" in columns)
    if basis is PnlBasis.NET and opts.kind is ImportKind.POSITIONS and "funding" not in columns:
        # realized = net + fee − funding: without funding the venue's figure is unknowable
        raise ImportRefused(IssueCode.NET_PNL_WITHOUT_FUNDING, "funding")

    checks: dict[int, tuple[Decimal, Decimal]] = {}
    if opts.layout is Layout.WEB_EXPORT:
        # The export states the net P/L as well: it must equal its own components.
        for row in parsed:
            v = row.values
            if v.get("pnl_net") is None:
                continue
            derived = v["pnl"] - v["fee"] + (v.get("funding") or Decimal(0))
            if v["pnl_net"] != derived:
                checks[row.line] = (v["pnl_net"], derived)
                report.add(
                    Issue(IssueCode.PNL_COMPONENTS_MISMATCH, Severity.WARNING, row.line, "pnl_net")
                )

    times = [
        t
        for row in parsed
        for t in (row.values.get("opened"), row.values.get("closed"), row.values.get("time"))
        if t is not None
    ]
    report.span_ms = (min(times), max(times)) if times else None

    if as_totals:
        report.add(Issue(IssueCode.KEPT_AS_TOTALS, Severity.INFO))
        if opts.covers is not None:
            report.add(Issue(IssueCode.COVERAGE_NOT_RECORDED, Severity.WARNING))
        return _fill_totals(parsed, opts)

    web = opts.layout is Layout.WEB_EXPORT
    out: list[_Prepared] = []
    for row in parsed:
        v = row.values
        if opts.kind is ImportKind.POSITIONS:
            assert basis is not None
            unknown = frozenset(f for f, src in _POSITION_OPTIONAL.items() if v.get(src) is None)
            out.append(
                _Prepared(row, _position(v, opts, basis), unknown, checks.get(row.line), web)
            )
        elif opts.kind is ImportKind.FILLS:
            unknown = frozenset(f for f, src in _FILL_OPTIONAL.items() if v.get(src) is None)
            out.append(_Prepared(row, _fill(v, opts, basis), unknown))
        else:
            key = journal_key(v)
            out.append(_Prepared(row, (key, _journal(v, basis, key)), frozenset()))
    return out


_TIME_FIELDS: Final = frozenset({"opened_ms", "updated_ms", "ts_ms"})


@dataclass(frozen=True)
class _Prepared:
    row: _Row
    record: Record | FillTotal | tuple[str, JournalEntry]
    #: Record fields the file left empty: a stored value for them is kept.
    unknown: frozenset[str]
    #: (stated, derived) net P/L when the file contradicts itself
    check: tuple[Decimal, Decimal] | None = None
    #: the file states a net P/L that was checked (a passing row clears an old check)
    checked: bool = False


def _fill_totals(rows: list[_Row], opts: ImportOptions) -> list[_Prepared]:
    """Fills without ids → one total per UTC day and symbol; the file's first and
    last day are marked incomplete (it may start or end inside them)."""
    groups: dict[tuple[str, str], list[_Row]] = {}
    for row in rows:
        groups.setdefault((utc_day(row.values["time"]), row.values["symbol"]), []).append(row)
    days = sorted({d for d, _ in groups})
    out: list[_Prepared] = []
    for (day, symbol), group in sorted(groups.items()):
        vs = [r.values for r in group]
        total = FillTotal(
            venue=opts.venue,
            account=opts.account,
            day=day,
            symbol=symbol,
            fills=len(vs),
            opens=sum(1 for v in vs if v["action"].startswith("open")),
            closes=sum(1 for v in vs if v["action"].startswith("close")),
            fee=sum((v["fee"] for v in vs), Decimal(0)),
            realized_pnl=sum((v["pnl"] for v in vs), Decimal(0)),
            complete=day not in (days[0], days[-1]),
        )
        out.append(_Prepared(group[0], total, frozenset()))
    return out


def _apply_fee_sign(rows: list[_Row], opts: ImportOptions, report: ImportReport) -> None:
    fees = [r.values["fee"] for r in rows if r.values.get("fee") is not None]
    flip = opts.fee_sign == "cost_negative"
    if opts.fee_sign == "auto" and fees:
        negatives = sum(1 for f in fees if f < 0)
        positives = sum(1 for f in fees if f > 0)
        if negatives and not positives:
            flip = True
        elif negatives and positives:
            report.add(Issue(IssueCode.MIXED_FEE_SIGNS, Severity.WARNING, field="fee"))
    if flip:
        report.fee_sign_flipped = True
        if opts.fee_sign == "auto":
            report.add(Issue(IssueCode.FEE_SIGN_FLIPPED, Severity.WARNING, field="fee"))
        for r in rows:
            if r.values.get("fee") is not None:
                r.values["fee"] = -r.values["fee"]


def _pnl_basis(
    rows: list[_Row], opts: ImportOptions, report: ImportReport, *, has_pnl: bool
) -> PnlBasis | None:
    if not has_pnl:
        return None
    if opts.layout is Layout.WEB_EXPORT:
        # Its P/L column is the gross one (the net one is checked separately).
        report.pnl_basis, report.pnl_basis_source = PnlBasis.GROSS, "layout"
        return PnlBasis.GROSS
    priced = opts.kind is not ImportKind.FILLS
    inferred = infer_pnl_basis([r.values for r in rows]) if priced else None
    if opts.pnl_basis is not None:
        report.pnl_basis, report.pnl_basis_source = opts.pnl_basis, "declared"
        if inferred is not None and inferred is not opts.pnl_basis:
            report.add(Issue(IssueCode.PNL_BASIS_DOUBTFUL, Severity.WARNING, field="pnl"))
        return opts.pnl_basis
    if inferred is None:
        raise ImportRefused(IssueCode.PNL_BASIS_REQUIRED, "pnl")
    report.pnl_basis, report.pnl_basis_source = inferred, "inferred"
    report.add(Issue(IssueCode.PNL_BASIS_INFERRED, Severity.WARNING, field="pnl"))
    return inferred


def _store(
    archive: LiveArchive,
    opts: ImportOptions,
    item: _Prepared,
    run_id: int,
    now: int,
    source: str,
    report: ImportReport,
) -> None:
    record = item.record
    if isinstance(record, tuple):
        key, entry = record
        report.counts[
            archive.upsert_journal(opts.journal, key, entry, run_id=run_id, now_ms=now)
        ] += 1
        return
    if isinstance(record, FillTotal):
        report.counts[archive.upsert_fill_total(record, run_id=run_id, source=source)] += 1
        return
    if isinstance(record, Position) and is_derived(record.position_id):
        twin = archive.position_twin(
            venue=record.venue,
            account=record.account,
            symbol=record.symbol,
            side=record.side,
            opened_ms=record.opened_ms,
        )
        if twin is not None:
            record = dataclasses.replace(record, position_id=twin)
            report.matched_by_identity += 1
    if item.checked:
        stated, derived = item.check if item.check else (None, None)
        archive.set_check(
            venue=record.venue,
            account=record.account,
            entity=Entity.POSITION,
            record_key=record.key(),
            code=Code.EXPORT_PNL_INCONSISTENT.value,
            stated=stated,
            derived=derived,
            line=item.row.line,
            run_id=run_id,
        )
    stored = archive.stored(record)
    if stored is not None:
        # What the file does not say is not a change: keep the stored value.
        keep = {f: getattr(stored[0], f) for f in item.unknown}
        # Nor is a time the file rounds to whole seconds.
        for f in _TIME_FIELDS & {x.name for x in dataclasses.fields(record)}:
            new, old = getattr(record, f), getattr(stored[0], f)
            if new % 1000 == 0 and new // 1000 == old // 1000:
                keep[f] = old
        record = dataclasses.replace(record, **keep)
    if stored is not None and not stored[1].startswith("csv:"):
        # The API is the primary source: a CSV row never overwrites what it delivered.
        if content_hash(stored[0]) == content_hash(record):
            report.counts[Outcome.UNCHANGED] += 1
        else:
            report.conflicts += 1
            report.add(Issue(IssueCode.CONFLICTS_WITH_API, Severity.WARNING, item.row.line))
        return
    report.counts[archive.upsert(record, run_id=run_id, now_ms=now, source=source)] += 1


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_ROWS",
    "ImportKind",
    "ImportOptions",
    "ImportRefused",
    "ImportReport",
    "Issue",
    "IssueCode",
    "Layout",
    "import_csv",
    "infer_pnl_basis",
    "journal_key",
    "parse_decimal",
    "parse_time",
    "resolve_columns",
    "to_gross",
]
