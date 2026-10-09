"""External signals (e.g. TradingView alerts) as time-stamped, untrusted facts.

An external signal is EVIDENCE, never an order: it is recorded, deduplicated
and later evaluated against independent market data (``signal_eval``). No
code path turns a received signal into a trade; a strategy may only use
signals that passed the same out-of-sample validation as everything else.

TradingView alert messages are free text the person writes into the alert
dialog. To be accepted here they must be JSON in this shape (TradingView
fills the ``{{...}}`` placeholders when the alert fires)::

    {"v": 1, "source": "tradingview", "indicator": "PVSRA", "signal": "climax_bull",
     "direction": "long", "symbol": "{{ticker}}", "exchange": "{{exchange}}",
     "interval": "{{interval}}", "bar_time": "{{time}}", "fired_at": "{{timenow}}",
     "price": {{close}}, "token": "<per-connection secret>"}

The ``token`` is checked by the receiving endpoint and never stored.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

DIRECTIONS: Final = frozenset({"long", "short", "neutral"})
MAX_TEXT: Final = 64
_NAME = re.compile(r"^[A-Za-z0-9 _./:+-]{1,64}$")
#: TradingView interval strings: "1", "15", "60", "240", "1D", "D", "1W", ...
_INTERVAL = re.compile(r"^(\d{1,4}[SDWM]?|[SDWM])$")


class SignalError(ValueError):
    """The payload is not a well-formed signal."""


@dataclass(frozen=True, slots=True)
class ExternalSignal:
    source: str  # "tradingview"
    indicator: str  # as named in the alert, e.g. "PVSRA", "Momentum"
    signal: str  # e.g. "climax_bull", "momentum_green"
    direction: str  # long / short / neutral
    symbol: str
    exchange: str
    interval: str
    bar_time_ms: int  # the bar the signal belongs to (TradingView {{time}} = bar open)
    fired_at_ms: int  # when the provider fired it ({{timenow}})
    received_at_ms: int  # when Jarvis received it
    price: float | None

    @property
    def key(self) -> str:
        """Stable identity: the same alert for the same bar is one signal."""
        raw = "|".join(
            (
                self.source,
                self.indicator,
                self.signal,
                self.symbol,
                self.exchange,
                self.interval,
                str(self.bar_time_ms),
            )
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @property
    def latency_ms(self) -> int:
        return self.received_at_ms - self.fired_at_ms

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {f: getattr(self, f) for f in self.__dataclass_fields__}
        out["key"] = self.key
        return out


def _ts(value: Any, name: str) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ms = int(value if value > 1e11 else value * 1000)
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise SignalError(f"{name} is not a timestamp") from None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)  # TradingView sends UTC
        ms = int(dt.timestamp() * 1000)
    else:
        raise SignalError(f"{name} missing")
    if not 1_262_304_000_000 <= ms <= 4_102_444_800_000:  # 2010..2100
        raise SignalError(f"{name} out of range")
    return ms


def _text(payload: Mapping[str, Any], name: str, *, default: str | None = None) -> str:
    value = payload.get(name, default)
    if not isinstance(value, str) or not _NAME.match(value.strip()):
        raise SignalError(f"{name} missing or malformed")
    return value.strip()


def parse_tradingview(payload: Mapping[str, Any], *, received_at_ms: int) -> ExternalSignal:
    """Validate one TradingView alert payload (see the module docstring)."""
    if payload.get("v") != 1:
        raise SignalError("unsupported payload version")
    if payload.get("source", "tradingview") != "tradingview":
        raise SignalError("not a TradingView payload")
    direction = str(payload.get("direction", "")).strip().lower()
    if direction not in DIRECTIONS:
        raise SignalError("direction must be long, short or neutral")
    interval = _text(payload, "interval").upper()
    if not _INTERVAL.match(interval):
        raise SignalError("interval malformed")
    price = payload.get("price")
    if price is not None:
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise SignalError("price must be a number")
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            raise SignalError("price must be positive")
    bar = _ts(payload.get("bar_time"), "bar_time")
    fired = _ts(payload.get("fired_at"), "fired_at")
    if fired < bar:
        raise SignalError("fired before its bar opened")
    if fired - received_at_ms > 5 * 60_000:
        raise SignalError("fired_at lies in the future")
    return ExternalSignal(
        "tradingview",
        _text(payload, "indicator"),
        _text(payload, "signal"),
        direction,
        _text(payload, "symbol").upper(),
        _text(payload, "exchange", default="UNKNOWN").upper(),
        interval,
        bar,
        fired,
        int(received_at_ms),
        price,
    )


class SignalLog:
    """In-memory signal record with duplicate suppression (by ``key``)."""

    def __init__(self) -> None:
        self._by_key: dict[str, ExternalSignal] = {}

    def add(self, signal: ExternalSignal) -> bool:
        """True when new; a repeated delivery of the same alert is ignored."""
        if signal.key in self._by_key:
            return False
        self._by_key[signal.key] = signal
        return True

    def all(self) -> list[ExternalSignal]:
        return sorted(self._by_key.values(), key=lambda s: (s.bar_time_ms, s.key))


__all__ = ["DIRECTIONS", "ExternalSignal", "SignalError", "SignalLog", "parse_tradingview"]
