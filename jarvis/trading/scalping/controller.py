"""The independent risk and portfolio controller for short-term trading.

It sits IN FRONT of each account's risk manager and can only refuse — never
loosen a limit. It sees every account's positions and closed trades and
enforces portfolio-wide rules that a single account cannot:

- trade frequency: at most N entries per UTC day in total and per strategy;
- loss streaks: after K consecutive losing trades a strategy cools down;
- daily loss: realised losses across all accounts against the total capital;
- concurrent exposure: number of open positions and total notional;
- correlated positions: same-direction positions in markets whose
  correlation is above a threshold (an unknown pair counts as correlated).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ControllerLimits:
    max_entries_per_day: int = 20
    max_entries_per_day_per_strategy: int = 10
    loss_streak: int = 3
    cooldown_ms: int = 60 * 60_000
    daily_loss_frac: float = 0.02  # of total capital, realised, all accounts
    max_concurrent: int = 2
    max_exposure_frac: float = 1.0  # total notional / total capital
    max_correlated_same_direction: int = 1
    correlation_threshold: float = 0.7


@dataclass
class _Open:
    strategy: str
    symbol: str
    side: str
    notional: float


@dataclass
class PortfolioController:
    total_capital: float
    limits: ControllerLimits = field(default_factory=ControllerLimits)
    correlations: Mapping[frozenset[str], float] = field(default_factory=dict)
    day: str = ""
    entries_today: Counter[str] = field(default_factory=Counter)
    realised_today: float = 0.0
    streak: Counter[str] = field(default_factory=Counter)
    cooldown_until: dict[str, int] = field(default_factory=dict)
    open: dict[tuple[str, str], _Open] = field(default_factory=dict)

    def _roll(self, now_ms: int) -> None:
        day = datetime.fromtimestamp(now_ms / 1000, UTC).date().isoformat()
        if day != self.day:
            self.day, self.entries_today, self.realised_today = day, Counter(), 0.0

    def _corr(self, a: str, b: str) -> float:
        return 1.0 if a == b else self.correlations.get(frozenset((a, b)), 1.0)

    def check_entry(
        self, strategy: str, symbol: str, side: str, notional: float, now_ms: int
    ) -> list[str]:
        """Reasons to refuse this entry; empty when it may go to the account."""
        self._roll(now_ms)
        lim, out = self.limits, []
        if self.entries_today["*"] >= lim.max_entries_per_day:
            out.append(f"portfolio entry limit reached ({lim.max_entries_per_day} today)")
        if self.entries_today[strategy] >= lim.max_entries_per_day_per_strategy:
            out.append(
                f"strategy entry limit reached ({lim.max_entries_per_day_per_strategy} today)"
            )
        if now_ms < self.cooldown_until.get(strategy, 0):
            out.append(f"cooling down after {lim.loss_streak} consecutive losses")
        if self.realised_today <= -lim.daily_loss_frac * self.total_capital:
            out.append("portfolio daily loss limit reached")
        if len(self.open) >= lim.max_concurrent:
            out.append(f"{lim.max_concurrent} positions already open across accounts")
        exposure = sum(p.notional for p in self.open.values()) + notional
        if exposure > lim.max_exposure_frac * self.total_capital * (1 + 1e-9):
            out.append("total exposure across accounts would exceed the limit")
        same = [
            p
            for p in self.open.values()
            if p.side == side and self._corr(p.symbol, symbol) >= lim.correlation_threshold
        ]
        if len(same) >= lim.max_correlated_same_direction:
            out.append("a correlated position in the same direction is already open")
        return out

    def on_open(self, strategy: str, symbol: str, side: str, notional: float, now_ms: int) -> None:
        self._roll(now_ms)
        self.entries_today["*"] += 1
        self.entries_today[strategy] += 1
        self.open[(strategy, symbol)] = _Open(strategy, symbol, side, notional)

    def on_close(self, strategy: str, symbol: str, net: float, now_ms: int) -> None:
        self._roll(now_ms)
        self.open.pop((strategy, symbol), None)
        self.realised_today += net
        self.total_capital += net  # exposure is measured against realised capital
        if net < 0:
            self.streak[strategy] += 1
            if self.streak[strategy] >= self.limits.loss_streak:
                self.cooldown_until[strategy] = now_ms + self.limits.cooldown_ms
                self.streak[strategy] = 0
        else:
            self.streak[strategy] = 0


__all__ = ["ControllerLimits", "PortfolioController"]
