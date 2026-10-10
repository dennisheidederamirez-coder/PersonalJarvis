"""Stable codes for the journal's free-text reasons.

The engine, the risk manager and the strategies write their reasons as
English sentences with numbers in them ("no breakout: close 82578.5 inside
the 20-bar range …"). The dashboard shows that text verbatim as the detail,
and a translatable label from the code returned here. Matching is by prefix
or fixed phrase, in order; anything unknown becomes ``other`` and still
shows its original text.
"""

from __future__ import annotations

from typing import Final

#: (code, alternatives): the first entry with an alternative whose parts all
#: occur in the lower-cased text wins.
_TABLE: Final[tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]] = (
    ("warming_up", (("warming up",),)),
    ("waiting_bar", (("no new closed bar",),)),
    ("data_problem", (("data:",),)),
    ("kill_switch", (("kill switch",),)),
    ("daily_loss_limit", (("daily loss limit",),)),
    ("no_crossover", (("no sma crossover",),)),
    ("no_breakout", (("no breakout",),)),
    ("no_extreme", (("no extreme",),)),
    ("holding", (("holding ",),)),
    ("breakout_up", (("broke the", "-bar high"),)),
    ("breakout_down", (("broke the", "-bar low"),)),
    ("cross_up", (("crossed above",),)),
    ("cross_down", (("crossed below",),)),
    ("channel_exit", (("fell below the",), ("rose above the",))),
    ("already_open", (("already open",),)),
    ("max_positions", (("maximum number of positions",),)),
    ("open_risk_limit", (("total open risk",),)),
    ("exposure_limit", (("gross exposure",),)),
    ("correlated_risk", (("correlated risk",),)),
    ("stop_distance", (("stop too close",), ("stop too far",), ("wrong side",))),
    ("position_too_small", (("position too small",),)),
    ("duplicate_order", (("duplicate order",),)),
    ("gap_through_stop", (("gapped through the stop",),)),
    ("risk_grew", (("risk grew",),)),
    ("liquidity_cap", (("insufficient liquidity",),)),
    ("margin", (("margin",), ("liquidation price",))),
    ("price_check", (("invalid price",), ("far from the market",))),
    ("analysis_only", (("analysis-only",), ("cannot be shorted",))),
)

#: Exit reasons a trade carries (``Trade.exit_reason``) that are not sentences.
_EXITS: Final = {
    "stop": "exit_stop",
    "take_profit": "exit_take_profit",
    "liquidation": "exit_liquidation",
    "kill switch": "kill_switch",
}

CODES: Final = (
    *(code for code, _ in _TABLE),
    *sorted(set(_EXITS.values()) - {c for c, _ in _TABLE}),
    "execution_fault",
    "other",
)


def classify(kind: str, text: str) -> str:
    """The code for one journal reason (``kind`` disambiguates bare words)."""
    low = text.strip().lower()
    if kind == "execution_failed":
        return "execution_fault"
    if low in _EXITS:
        return _EXITS[low]
    for code, alternatives in _TABLE:
        if any(all(part in low for part in alt) for alt in alternatives):
            return code
    return "other"


__all__ = ["CODES", "classify"]
