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
    COMMODITY = "commodity"  # e.g. gold; never conflated with gold-backed tokens


class Session(StrEnum):
    ALWAYS = "24/7"  # crypto: no closing auction, no weekend
    EXCHANGE = "exchange"  # stocks/ETFs: exchange hours, gaps over nights/weekends
    OTC_24_5 = "24/5"  # spot gold (OTC): ~24 h on weekdays, closed on weekends


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
    #: maintenance margin rate of the simulated perpetual (lowest venue tier)
    mmr: float = 0.005

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


def coin(base: str, qty_step: float, *, mmr: float = 0.01) -> Instrument:
    """A research-stage altcoin: analysed and backtested, NOT demo-tradable
    until its own data check and validation say so (phase 3 gate). Altcoins
    get a higher simulated maintenance margin than BTC/ETH."""
    return Instrument(
        f"{base}-USD",
        AssetClass.CRYPTO,
        base,
        "USD",
        Session.ALWAYS,
        qty_step,
        demo_tradable=False,
        mmr=mmr,
    )


SOL_USD: Final = coin("SOL", 1e-2)
XRP_USD: Final = coin("XRP", 1.0)
BNB_USD: Final = coin("BNB", 1e-3)
LINK_USD: Final = coin("LINK", 1e-1)
#: Priority altcoins for the next analysis phase. HYPE
#: started trading in late November 2024: its history is short and every
#: test must start at its real listing, never earlier.
HYPE_USD: Final = coin("HYPE", 1e-2)
HBAR_USD: Final = coin("HBAR", 1.0)

#: Phase 3 research universe (analysis first; trading only after validation).
RESEARCH_UNIVERSE: Final[tuple[Instrument, ...]] = (
    SOL_USD,
    XRP_USD,
    BNB_USD,
    LINK_USD,
    HYPE_USD,
    HBAR_USD,
)
PRIORITY_ALTCOINS: Final[tuple[Instrument, ...]] = (HYPE_USD, HBAR_USD)

#: Gold in its distinct forms (phase 4). They are different instruments with
#: different prices, hours, costs and risks, and are never treated as one:
#: spot gold (OTC, 24/5), exchange-traded gold futures (expiries, sessions),
#: and gold-backed crypto tokens (24/7, issuer and peg risk).
XAU_SPOT: Final = Instrument(
    "XAU-USD",
    AssetClass.COMMODITY,
    "XAU",
    "USD",
    Session.OTC_24_5,
    1e-2,
    demo_tradable=False,
)
GOLD_FUTURES: Final = Instrument(
    "GC",
    AssetClass.COMMODITY,
    "GC",
    "USD",
    Session.EXCHANGE,
    1.0,
    demo_tradable=False,
)
PAXG_USD: Final = coin("PAXG", 1e-4)  # a token: crypto asset class, not gold itself
XAUT_USD: Final = coin("XAUT", 1e-4)


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


__all__ = [
    "BNB_USD",
    "BTC_USD",
    "DEMO_UNIVERSE",
    "ETH_USD",
    "GOLD_FUTURES",
    "HBAR_USD",
    "HYPE_USD",
    "LINK_USD",
    "PAXG_USD",
    "PRIORITY_ALTCOINS",
    "RESEARCH_UNIVERSE",
    "SOL_USD",
    "XAUT_USD",
    "XAU_SPOT",
    "XRP_USD",
    "AssetClass",
    "Instrument",
    "Session",
    "coin",
    "stock",
]
