"""Venue payloads (documented shapes) → LIVE records.

Strict on what identifies a record, lenient on optional extras: a missing
id, side or required amount raises ``BadPayload`` (the sync counts the item
as rejected and does not mark its window covered); an unknown extra field is
ignored. Numbers arrive as decimal strings and stay exact.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from jarvis.live_history.models import Balance, Fill, Order, Position
from jarvis.live_history.symbols import canonical


class BadPayload(ValueError):
    """An item that does not have the documented shape; carries the field name only."""


def _dec(item: dict[str, Any], key: str) -> Decimal:
    value = item.get(key)
    if value is None or value == "":
        raise BadPayload(f"missing {key}")
    try:
        out = Decimal(str(value))
    except InvalidOperation:  # re-raised as BadPayload with the field name
        raise BadPayload(f"not a number: {key}") from None
    if not out.is_finite():
        raise BadPayload(f"not finite: {key}")
    return out


def _dec_or_none(item: dict[str, Any], key: str) -> Decimal | None:
    return None if item.get(key) in (None, "") else _dec(item, key)


def _dec_or_zero(item: dict[str, Any], key: str) -> Decimal:
    return Decimal(0) if item.get(key) in (None, "") else _dec(item, key)


def _text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if value is None or str(value).strip() == "":
        raise BadPayload(f"missing {key}")
    return str(value).strip()


def _text_or_none(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    return None if value is None or str(value).strip() == "" else str(value).strip()


def _ms(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise BadPayload(f"missing {key}")
    try:
        return int(value)
    except ValueError:  # re-raised as BadPayload with the field name
        raise BadPayload(f"not a timestamp: {key}") from None


def _choice(item: dict[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = _text(item, key).lower()
    if value not in allowed:
        raise BadPayload(f"unexpected {key}")
    return value


def _int_or_none(item: dict[str, Any], key: str) -> int | None:
    value = item.get(key)
    try:
        return None if value in (None, "") else int(value)
    except (TypeError, ValueError):  # re-raised as BadPayload with the field name
        raise BadPayload(f"not an integer: {key}") from None


_SIDES = frozenset({"long", "short"})
_TRADE_SIDES = frozenset({"buy", "sell"})


def history_position(item: dict[str, Any], *, venue: str, account: str) -> Position:
    """An entry of ``get_history_positions`` → a closed position."""
    liq = _dec_or_none(item, "liqQty")
    return Position(
        venue=venue,
        account=account,
        position_id=_text(item, "positionId"),
        symbol=canonical(_text(item, "symbol")),
        side=_choice(item, "side", _SIDES),
        status="closed",
        qty=_dec(item, "maxQty"),
        entry_price=_dec(item, "entryPrice"),
        close_price=_dec_or_none(item, "closePrice"),
        fee=_dec_or_zero(item, "fee"),
        funding=_dec_or_zero(item, "funding"),
        realized_pnl=_dec(item, "realizedPNL"),
        unrealized_pnl=None,
        liq_qty=liq,
        leverage=_int_or_none(item, "leverage"),
        margin_mode=_text_or_none(item, "marginMode"),
        opened_ms=_ms(item, "ctime"),
        updated_ms=_ms(item, "mtime"),
    )


def pending_position(item: dict[str, Any], *, venue: str, account: str) -> Position:
    """An entry of ``get_pending_positions`` → an open position."""
    return Position(
        venue=venue,
        account=account,
        position_id=_text(item, "positionId"),
        symbol=canonical(_text(item, "symbol")),
        side=_choice(item, "side", _SIDES),
        status="open",
        qty=_dec(item, "qty"),
        entry_price=_dec(item, "avgOpenPrice"),
        close_price=None,
        fee=_dec_or_zero(item, "fee"),
        funding=_dec_or_zero(item, "funding"),
        realized_pnl=_dec_or_zero(item, "realizedPNL"),
        unrealized_pnl=_dec_or_none(item, "unrealizedPNL"),
        liq_qty=None,
        leverage=_int_or_none(item, "leverage"),
        margin_mode=_text_or_none(item, "marginMode"),
        opened_ms=_ms(item, "ctime"),
        updated_ms=_ms(item, "mtime"),
    )


def fill(item: dict[str, Any], *, venue: str, account: str) -> Fill:
    """An entry of ``get_history_trades``."""
    return Fill(
        venue=venue,
        account=account,
        trade_id=_text(item, "tradeId"),
        order_id=_text(item, "orderId"),
        position_id=_text_or_none(item, "positionId"),
        symbol=canonical(_text(item, "symbol")),
        # The venue's field table lists ``side`` but its example omits it.
        side=_choice(item, "side", _TRADE_SIDES) if item.get("side") else None,
        qty=_dec(item, "qty"),
        price=_dec(item, "price"),
        fee=_dec_or_zero(item, "fee"),
        realized_pnl=_dec_or_zero(item, "realizedPNL"),
        role=(_text_or_none(item, "roleType") or "").lower() or None,
        reduce_only=bool(item.get("reduceOnly", False)),
        client_id=_text_or_none(item, "clientId"),
        ts_ms=_ms(item, "ctime"),
    )


def order(item: dict[str, Any], *, venue: str, account: str) -> Order:
    """An entry of ``get_history_orders`` or ``get_pending_orders``."""
    side = _text_or_none(item, "side")
    otype = _text_or_none(item, "orderType") or _text_or_none(item, "type")
    return Order(
        venue=venue,
        account=account,
        order_id=_text(item, "orderId"),
        client_id=_text_or_none(item, "clientId"),
        symbol=canonical(_text(item, "symbol")),
        side=side.lower() if side else None,
        order_type=otype.lower() if otype else None,
        status=_text(item, "status").lower(),
        qty=_dec(item, "qty"),
        filled_qty=_dec_or_zero(item, "tradeQty"),
        price=_dec_or_none(item, "price"),
        fee=_dec_or_zero(item, "fee"),
        realized_pnl=_dec_or_zero(item, "realizedPNL"),
        tp_price=_dec_or_none(item, "tpPrice"),
        sl_price=_dec_or_none(item, "slPrice"),
        reduce_only=bool(item.get("reduceOnly", False)),
        created_ms=_ms(item, "ctime"),
        updated_ms=_ms(item, "mtime"),
    )


def balance(item: dict[str, Any], *, venue: str, account: str, ts_ms: int) -> Balance:
    """An entry of ``account`` (the venue answers with a list, one per margin coin)."""
    return Balance(
        venue=venue,
        account=account,
        margin_coin=_text(item, "marginCoin").upper(),
        ts_ms=ts_ms,
        available=_dec(item, "available"),
        frozen=_dec_or_zero(item, "frozen"),
        margin=_dec_or_zero(item, "margin"),
        transfer=_dec_or_zero(item, "transfer"),
        cross_unrealized=_dec_or_zero(item, "crossUnrealizedPNL"),
        isolation_unrealized=_dec_or_zero(item, "isolationUnrealizedPNL"),
        bonus=_dec_or_zero(item, "bonus"),
    )


__all__ = [
    "BadPayload",
    "balance",
    "fill",
    "history_position",
    "order",
    "pending_position",
]
