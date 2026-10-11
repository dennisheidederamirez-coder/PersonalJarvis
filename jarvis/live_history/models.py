"""The normalized LIVE records: what the archive stores and the reconciler compares.

Money and quantities are ``Decimal`` (the venue sends decimal strings; float
rounding would invent differences the reconciler then reports). Times are UTC
milliseconds. Every record names its ``venue`` and ``account`` and has a
stable ``key()`` — the venue's own id — which is what makes a re-fetched
record a duplicate rather than a new one.

There is deliberately no ``mode`` field: these types are LIVE by definition,
and paper results never become one of them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar, Final


class Entity(StrEnum):
    POSITION = "position"
    FILL = "fill"
    ORDER = "order"
    BALANCE = "balance"


@dataclass(frozen=True)
class Position:
    """One venue position, open or closed (the same ``position_id`` moves from open to closed).

    ``realized_pnl`` is the venue's figure, which EXCLUDES fees and funding;
    ``net`` adds them. ``funding`` is signed (negative = paid).
    """

    venue: str
    account: str
    position_id: str
    symbol: str  # canonical, see ``symbols.canonical``
    side: str  # long | short
    status: str  # open | closed
    qty: Decimal  # open: current size; closed: the largest size it reached
    entry_price: Decimal
    close_price: Decimal | None
    fee: Decimal
    funding: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None
    liq_qty: Decimal | None
    leverage: int | None
    margin_mode: str | None
    opened_ms: int
    updated_ms: int

    entity: ClassVar[Entity] = Entity.POSITION
    #: Marks that move with the market; a change here refreshes the stored
    #: copy but is not a revision of the position.
    volatile: ClassVar[frozenset[str]] = frozenset({"unrealized_pnl"})

    def key(self) -> str:
        return self.position_id

    def sort_ms(self) -> int:
        return self.opened_ms

    @property
    def net(self) -> Decimal:
        return self.realized_pnl - self.fee + self.funding


@dataclass(frozen=True)
class Fill:
    venue: str
    account: str
    trade_id: str
    order_id: str
    position_id: str | None  # the venue documents it; tolerate its absence
    symbol: str
    side: str | None  # buy | sell
    qty: Decimal
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    role: str | None  # maker | taker
    reduce_only: bool
    client_id: str | None
    ts_ms: int

    entity: ClassVar[Entity] = Entity.FILL
    volatile: ClassVar[frozenset[str]] = frozenset()

    def key(self) -> str:
        return self.trade_id

    def sort_ms(self) -> int:
        return self.ts_ms


@dataclass(frozen=True)
class Order:
    venue: str
    account: str
    order_id: str
    client_id: str | None
    symbol: str
    side: str | None  # buy | sell
    order_type: str | None  # limit | market
    status: str  # new | part_filled | filled | canceled | …
    qty: Decimal
    filled_qty: Decimal
    price: Decimal | None
    fee: Decimal
    realized_pnl: Decimal
    tp_price: Decimal | None
    sl_price: Decimal | None
    reduce_only: bool
    created_ms: int
    updated_ms: int

    entity: ClassVar[Entity] = Entity.ORDER
    volatile: ClassVar[frozenset[str]] = frozenset()

    def key(self) -> str:
        return self.order_id

    def sort_ms(self) -> int:
        return self.created_ms


@dataclass(frozen=True)
class Balance:
    """One observation of the futures account; each sync adds one."""

    venue: str
    account: str
    margin_coin: str
    ts_ms: int  # when Jarvis observed it
    available: Decimal
    frozen: Decimal  # locked by open orders
    margin: Decimal  # locked by positions
    transfer: Decimal
    cross_unrealized: Decimal
    isolation_unrealized: Decimal
    bonus: Decimal

    entity: ClassVar[Entity] = Entity.BALANCE
    volatile: ClassVar[frozenset[str]] = frozenset()

    def key(self) -> str:
        return f"{self.margin_coin}:{self.ts_ms}"

    def sort_ms(self) -> int:
        return self.ts_ms

    @property
    def wallet(self) -> Decimal:
        """Balance without unrealized results: what fees, funding and closes move."""
        return self.available + self.frozen + self.margin


Record = Position | Fill | Order | Balance

TYPES: Final[dict[Entity, type[Position] | type[Fill] | type[Order] | type[Balance]]] = {
    Entity.POSITION: Position,
    Entity.FILL: Fill,
    Entity.ORDER: Order,
    Entity.BALANCE: Balance,
}


def to_data(record: Record) -> dict[str, Any]:
    """JSON-safe dict: ``Decimal`` as a plain string without trailing zeros.

    ``"102.90"`` and ``"102.9"`` are the same amount; writing both the same way
    keeps a cosmetic difference from looking like a revision.
    """
    out: dict[str, Any] = {}
    for f in dataclasses.fields(record):
        value = getattr(record, f.name)
        out[f.name] = decimal_text(value) if isinstance(value, Decimal) else value
    return out


def decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def from_data(entity: Entity, data: dict[str, Any]) -> Record:
    cls = TYPES[entity]
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        value = data.get(f.name)
        if value is not None and "Decimal" in str(f.type):
            value = Decimal(value)
        kwargs[f.name] = value
    return cls(**kwargs)


def content_hash(record: Record) -> str:
    """Identity of a record's meaning: volatile marks excluded, key order fixed."""
    data = {k: v for k, v in to_data(record).items() if k not in record.volatile}
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


__all__ = [
    "TYPES",
    "Balance",
    "Entity",
    "Fill",
    "Order",
    "Position",
    "Record",
    "content_hash",
    "decimal_text",
    "from_data",
    "to_data",
]
