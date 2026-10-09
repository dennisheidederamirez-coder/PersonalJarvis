"""Multi-asset instruments. Crypto is demo-tradable; stocks and ETFs are
modelled so research and data handling work for them, but the risk manager
refuses to trade them until a later phase enables it explicitly."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    STOCK = "stock"
    ETF = "etf"


class Session(StrEnum):
    ALWAYS = "24/7"  # crypto: no closing auction, no weekend
    EXCHANGE = "exchange"  # stocks/ETFs: exchange hours, gaps over nights/weekends


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str  # e.g. "BTC-USD", "AAPL"
    asset_class: AssetClass
    base: str
    quote: str
    session: Session
    #: smallest tradable quantity step; orders are rounded down to it
    qty_step: float = 1e-6
    #: whether the demo engine may open positions in it (phase gate)
    demo_tradable: bool = False
    #: whether a short position is possible in the simulation (perp-style)
    shortable: bool = True

    def round_qty(self, qty: float) -> float:
        if qty <= 0 or self.qty_step <= 0:
            return 0.0
        steps = int(qty / self.qty_step + 1e-9)
        return round(steps * self.qty_step, 12)


BTC_USD: Final = Instrument(
    "BTC-USD", AssetClass.CRYPTO, "BTC", "USD", Session.ALWAYS, 1e-5, demo_tradable=True
)
ETH_USD: Final = Instrument(
    "ETH-USD", AssetClass.CRYPTO, "ETH", "USD", Session.ALWAYS, 1e-4, demo_tradable=True
)

#: Phase 1 universe. Altcoins join later, one by one, with their own review.
DEMO_UNIVERSE: Final[tuple[Instrument, ...]] = (BTC_USD, ETH_USD)


def stock(symbol: str, *, etf: bool = False) -> Instrument:
    """An analysis-only stock or ETF (phase 2: analyse, do not trade)."""
    return Instrument(
        symbol.upper(),
        AssetClass.ETF if etf else AssetClass.STOCK,
        symbol.upper(),
        "USD",
        Session.EXCHANGE,
        1.0,
        demo_tradable=False,
        shortable=False,
    )


__all__ = ["BTC_USD", "DEMO_UNIVERSE", "ETH_USD", "AssetClass", "Instrument", "Session", "stock"]
