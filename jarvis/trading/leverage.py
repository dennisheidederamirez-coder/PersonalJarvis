"""Leverage for SIMULATED crypto futures: tiers, margin and liquidation.

Leverage never changes how much a trade may lose: the risk manager still
sizes every position from the stop distance (``risk_per_trade``). Leverage
only decides how much margin the position ties up — and therefore where it
would be liquidated. A higher leverage is never a bigger bet, only a thinner
cushion, so it is gated:

=========  ==================================================================
1-5x       initial demo range (default ceiling)
<= 10x     only for a strategy validated walk-forward AT that leverage
<= 20x     additionally reviewed separately (an explicit flag)
<= 30x     experimental simulation, only with the owner's explicit approval
> 30x      never
=========  ==================================================================

The leverage of a strategy is a fixed, configured parameter. Nothing raises
it at runtime — no strategy target, no confidence score, no model.

Liquidation (isolated margin, linear USDT-margined perpetual, simplified):
the position is liquidated when its loss eats the initial margin down to the
maintenance margin plus a reserve for the closing fee::

    long:  liq = entry - (IM - MM - fee_reserve) / qty
    short: liq = entry + (IM - MM - fee_reserve) / qty
    IM = entry * qty / leverage,  MM = entry * qty * mmr,
    fee_reserve = entry * qty * fee_rate

Real venues use tiered maintenance rates and the mark price; this is a
conservative simulation, not a venue's exact formula.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from jarvis.trading.strategies import Side

ABSOLUTE_MAX: Final = 30.0
INITIAL_MAX: Final = 5.0
VALIDATED_MAX: Final = 10.0
REVIEWED_MAX: Final = 20.0


@dataclass(frozen=True, slots=True)
class LeverageGrant:
    """What one strategy is configured to use, and the evidence behind it."""

    leverage: float = 1.0
    validated: bool = False  # walk-forward verdict tradable AT this leverage
    reviewed: bool = False  # separately reviewed (needed above 10x)
    experimental_approval: bool = False  # owner's explicit approval (needed above 20x)


def leverage_reasons(grant: LeverageGrant) -> tuple[str, ...]:
    """Why *grant* is not allowed; empty when it is."""
    lev = grant.leverage
    if not lev >= 1.0:
        return ("leverage below 1x",)
    if lev > ABSOLUTE_MAX:
        return (f"leverage above {ABSOLUTE_MAX:.0f}x is never allowed",)
    out: list[str] = []
    if lev > INITIAL_MAX and not grant.validated:
        out.append(f"above {INITIAL_MAX:.0f}x needs a strategy validated at this leverage")
    if lev > VALIDATED_MAX and not grant.reviewed:
        out.append(f"above {VALIDATED_MAX:.0f}x needs a separate review")
    if lev > REVIEWED_MAX and not grant.experimental_approval:
        out.append(f"above {REVIEWED_MAX:.0f}x needs the owner's explicit experimental approval")
    return tuple(out)


def initial_margin(entry: float, qty: float, leverage: float) -> float:
    return abs(entry * qty) / leverage


def liquidation_price(
    side: Side, entry: float, qty: float, leverage: float, *, mmr: float, fee_rate: float
) -> float:
    """Isolated-margin liquidation price (see the module docstring); 0 if none."""
    if qty <= 0 or leverage < 1:
        raise ValueError("qty must be positive and leverage >= 1")
    im = initial_margin(entry, qty, leverage)
    cushion = im - entry * qty * mmr - entry * qty * fee_rate
    liq = entry - side.sign * cushion / qty
    return max(0.0, liq)


def stop_before_liquidation(
    side: Side, entry: float, stop: float, liquidation: float, *, buffer: float
) -> bool:
    """The stop must lie between entry and liquidation, and use at most
    ``1 - buffer`` of that distance (``buffer=0.5``: the stop is hit before
    half of the way to liquidation)."""
    room = side.sign * (entry - liquidation)
    used = side.sign * (entry - stop)
    if room <= 0 or used <= 0:
        return False
    return used <= room * (1 - buffer)


__all__ = [
    "ABSOLUTE_MAX",
    "INITIAL_MAX",
    "REVIEWED_MAX",
    "VALIDATED_MAX",
    "LeverageGrant",
    "initial_margin",
    "leverage_reasons",
    "liquidation_price",
    "stop_before_liquidation",
]
