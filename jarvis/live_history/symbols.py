"""One canonical symbol for the spellings venues and journals use.

``BTCUSDT``, ``BTC/USDT``, ``btc-usdt``, ``BTCUSDT.P``, ``BTCUSDT PERP`` and
``BTC/USDT:USDT`` all name the same USDT-margined perpetual; the canonical
form is the venue's own ``BTCUSDT``. A spelling that cannot be split into a
known quote currency is returned upper-cased and unchanged, never guessed.
"""

from __future__ import annotations

import re
from typing import Final

#: Longest first, so ``USDC`` is not read as ``USD`` + ``C``.
QUOTES: Final = ("USDT", "USDC", "FDUSD", "USD")

_SUFFIX = re.compile(r"(\.P|[-_ ]?PERP(ETUAL)?|[-_ ]?SWAP)$")
_SETTLE = re.compile(r":[A-Z]+$")


def canonical(raw: str) -> str:
    """``" btc/usdt:usdt "`` → ``"BTCUSDT"``."""
    s = raw.strip().upper()
    s = _SETTLE.sub("", s)
    s = _SUFFIX.sub("", s)
    return re.sub(r"[/\-_ ]", "", s)


def split(raw: str) -> tuple[str, str] | None:
    """``"BTC/USDT"`` → ``("BTC", "USDT")``; ``None`` when no known quote fits."""
    s = canonical(raw)
    for quote in QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)], quote
    return None


__all__ = ["QUOTES", "canonical", "split"]
