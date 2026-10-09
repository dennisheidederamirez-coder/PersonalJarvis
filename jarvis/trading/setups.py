"""The strategy book: which strategies may trade, and how competing setups
are ranked — by objective, pre-computed numbers, never by a model's opinion.

- Only strategies whose walk-forward verdict says ``tradable`` are in the
  active book. Every strategy — rule-based or built on external signals — is
  validated and judged separately; none needs another one's confirmation.
- When several active strategies propose an entry on the same bar, the
  setups are ranked by the strategy's validated out-of-sample expectancy
  (average R per trade after costs), then by its p-value, then by the
  setup's reward-to-risk. The risk manager gets them in that order and
  approves at most what its limits allow.
- An external signal (e.g. a TradingView alert) that agrees with a setup is
  recorded as ``confirmed_by`` for later analysis. It is information, not a
  requirement, and it does not change the rank unless a strategy that uses
  it has itself been validated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from jarvis.trading.data import BarSeries
from jarvis.trading.engine import Candidate
from jarvis.trading.instruments import Instrument
from jarvis.trading.signals import ExternalSignal, SignalLog
from jarvis.trading.strategies import Strategy, Target
from jarvis.trading.validation import Verdict

_QUOTES: Final = ("", "USD", "USDT", "USDC", "PERP", "USDTP", "USDTPERP", "USDPERP")


def symbol_matches(provider_symbol: str, instrument: Instrument) -> bool:
    """``BTCUSDT``, ``BTCUSDT.P``, ``BTC-USD`` all describe BTC against a dollar."""
    norm = re.sub(r"[^A-Z]", "", provider_symbol.upper())
    if norm.startswith("XBT"):
        norm = "BTC" + norm[3:]
    base = instrument.base.upper()
    return norm.startswith(base) and norm[len(base) :] in _QUOTES


@dataclass(frozen=True, slots=True)
class Setup:
    strategy: str
    symbol: str
    side: str
    entry: float
    stop: float
    take_profit: float | None
    reward_risk: float | None
    edge_r: float  # the strategy's validated out-of-sample expectancy (R per trade)
    p_value: float
    confirmed_by: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


@dataclass
class StrategyBook:
    confirmations: SignalLog | None = None
    confirm_window_bars: int = 3
    _entries: dict[str, tuple[Strategy, Verdict]] = field(default_factory=dict)

    def add(self, strategy: Strategy, verdict: Verdict) -> None:
        if strategy.name in self._entries:
            raise ValueError(f"strategy {strategy.name!r} already in the book")
        self._entries[strategy.name] = (strategy, verdict)

    def active(self) -> list[Strategy]:
        """The strategies allowed to open demo trades."""
        return [s for s, v in self._entries.values() if v.tradable]

    def verdicts(self) -> dict[str, Verdict]:
        return {name: v for name, (_s, v) in self._entries.items()}

    def setups(self, series: BarSeries, i: int, candidates: Iterable[Candidate]) -> list[Setup]:
        out: list[Setup] = []
        close = float(series.close[i])
        for strategy, target in candidates:
            entry = self._entries.get(strategy.name)
            if entry is None or not entry[1].tradable or target.side is None:
                continue  # unvalidated strategies never trade
            if target.stop is None:
                continue
            verdict = entry[1]
            risk = abs(close - target.stop)
            rr = abs(target.take_profit - close) / risk if target.take_profit and risk else None
            out.append(
                Setup(
                    strategy.name,
                    series.instrument.symbol,
                    target.side.value,
                    close,
                    target.stop,
                    target.take_profit,
                    rr,
                    verdict.oos_expectancy_r,
                    verdict.p_value,
                    self._confirmed(series, i, target),
                    target.reason,
                )
            )
        out.sort(key=lambda s: (-s.edge_r, s.p_value, -(s.reward_risk or 0.0), s.strategy))
        return out

    def rank(self, series: BarSeries, i: int, candidates: list[Candidate]) -> list[Candidate]:
        """The engine's ranker: validated candidates, best setup first."""
        by_name: dict[str, tuple[Strategy, Target]] = {s.name: (s, t) for s, t in candidates}
        return [by_name[s.strategy] for s in self.setups(series, i, candidates)]

    def _confirmed(self, series: BarSeries, i: int, target: Target) -> tuple[str, ...]:
        if self.confirmations is None or target.side is None:
            return ()
        lo = int(series.ts[max(0, i - self.confirm_window_bars + 1)])
        hi = int(series.ts[i]) + series.interval_ms
        found = []
        for sig in self.confirmations.all():
            if (
                lo <= sig.bar_time_ms < hi
                and sig.received_at_ms <= hi
                and sig.direction == target.side.value
                and symbol_matches(sig.symbol, series.instrument)
            ):
                found.append(f"{sig.indicator}:{sig.signal}")
        return tuple(sorted(set(found)))


def signals_for(signals: Iterable[ExternalSignal], instrument: Instrument) -> list[ExternalSignal]:
    return [s for s in signals if symbol_matches(s.symbol, instrument)]


__all__ = ["Setup", "StrategyBook", "signals_for", "symbol_matches"]
