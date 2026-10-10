"""Named risk profiles. The conservative profile is the only one usable
without the owner: 1x leverage, at most 0.5 % of the account at risk per
trade. Every other profile is LOCKED. It opens only with a written owner
approval for that very profile and a pre-registered spec, and it still has
to pass the leverage tiers (``leverage.leverage_reasons``) — e.g. 10x needs
a strategy validated at 10x. Nothing selects a profile automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from jarvis.trading.leverage import LeverageGrant, leverage_reasons
from jarvis.trading.risk import RiskLimits


class ProfileLocked(PermissionError):
    """The profile needs the owner's explicit approval."""


@dataclass(frozen=True, slots=True)
class RiskProfile:
    name: str
    leverage: float
    risk_per_trade: float
    max_open_risk: float
    daily_loss_limit: float
    max_drawdown: float
    locked: bool
    note: str = ""
    min_stop_distance: float = 0.002
    round_trip_cost: float = 0.0


@dataclass(frozen=True, slots=True)
class Approval:
    profile: str
    by: str  # who approved
    spec_digest: str  # the pre-registered spec it is bound to
    validated_at_leverage: bool = False  # walk-forward verdict at this leverage
    reviewed: bool = False  # separate review (needed above 10x)


CONSERVATIVE: Final = RiskProfile(
    "conservative", 1.0, 0.005, 0.015, 0.02, 0.10, locked=False, note="default for every test"
)
#: The conservative profile at scalping scale: the SAME loss limits, a smaller
#: minimum stop distance — and the round-trip cost counted into every stop, so
#: the loss at a stop including fees, spread and slippage stays within 0.5 %.
CONSERVATIVE_SCALP: Final = RiskProfile(
    "conservative-scalp",
    1.0,
    0.005,
    0.015,
    0.02,
    0.10,
    locked=False,
    note="1m-5m research; costs counted into the stop",
    min_stop_distance=0.0005,
    round_trip_cost=0.0015,
)
DEMO_10X_5PCT: Final = RiskProfile(
    "demo-10x-5pct",
    10.0,
    0.05,
    0.10,
    0.10,
    0.25,
    locked=True,
    note="separate demo profile; locked until the owner approves it for one spec",
)

PROFILES: Final = {p.name: p for p in (CONSERVATIVE, CONSERVATIVE_SCALP, DEMO_10X_5PCT)}


def resolve(
    profile: RiskProfile, approval: Approval | None = None
) -> tuple[RiskLimits, LeverageGrant]:
    """The account limits and leverage grant for *profile* — or ProfileLocked."""
    if profile.locked:
        if approval is None or approval.profile != profile.name or not approval.by.strip():
            raise ProfileLocked(f"profile {profile.name!r} needs the owner's explicit approval")
        if len(approval.spec_digest) != 64:
            raise ProfileLocked("the approval must name a pre-registered spec hash")
    grant = LeverageGrant(
        profile.leverage,
        validated=bool(approval and approval.validated_at_leverage),
        reviewed=bool(approval and approval.reviewed),
    )
    blocked = leverage_reasons(grant)
    if blocked:
        raise ProfileLocked("; ".join(blocked))
    limits = RiskLimits(
        risk_per_trade=profile.risk_per_trade,
        max_open_risk=profile.max_open_risk,
        daily_loss_limit=profile.daily_loss_limit,
        max_drawdown=profile.max_drawdown,
        max_gross_exposure=max(1.0, profile.leverage),
        min_stop_distance=profile.min_stop_distance,
        round_trip_cost=profile.round_trip_cost,
    )
    return limits, grant


__all__ = [
    "CONSERVATIVE",
    "CONSERVATIVE_SCALP",
    "DEMO_10X_5PCT",
    "PROFILES",
    "Approval",
    "ProfileLocked",
    "RiskProfile",
    "resolve",
]
