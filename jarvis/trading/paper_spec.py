"""The pre-registered forward paper test (docs/trading-paper-test-spec.md).

Everything that decides a trade is frozen here: strategies, parameters,
markets, intervals, costs, risk limits, leverage. ``digest()`` hashes the
canonical form; the runner refuses to start when the expected hash or the
hash stored in its journal differs. Any change therefore starts a NEW test.

Each candidate trades its own virtual account (with the full, unchanged
risk limits), so candidate A's BTC position never blocks candidate B and
the two stay separately attributable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Final

from jarvis.trading.instruments import BNB_USD, BTC_USD, ETH_USD, SOL_USD, XRP_USD, Instrument
from jarvis.trading.timeframes import D1, H4, SUPPORTED

MAX_RISK_PER_TRADE: Final = 0.005


class SpecError(ValueError):
    """The specification breaks a pre-registered rule."""


@dataclass(frozen=True)
class StreamSpec:
    candidate: str  # "A" / "B"
    symbol: str
    interval_ms: int
    strategy: str  # "donchian_breakout" / "sma_cross"
    params: dict[str, Any]
    slippage_bps: float
    spread_bps: float = 2.0
    primary_source: str = ""  # e.g. "binance:perp:BTCUSDT"
    backup_source: str = ""  # e.g. "okx:perp:BTC-USDT-SWAP"

    @property
    def id(self) -> str:
        return f"{self.candidate}:{self.symbol}"


@dataclass(frozen=True)
class PaperSpec:
    name: str
    version: int
    streams: tuple[StreamSpec, ...]
    capital_per_candidate: float = 10_000.0
    leverage: float = 1.0
    risk_per_trade: float = 0.005
    max_open_risk: float = 0.015
    daily_loss_limit: float = 0.02
    max_drawdown: float = 0.10
    max_gross_exposure: float = 1.0
    max_positions: int = 3
    fee_rate: float = 0.0005
    max_data_age_bars: int = 2
    max_volume_share: float = 0.01  # an order uses at most 1 % of the prior bar's volume
    min_trades_to_evaluate: int = 40
    success: dict[str, Any] = field(
        default_factory=lambda: {
            "profit_factor_min": 1.2,
            "avg_r_min_exclusive": 0.0,
            "p_value_max": 0.025,
            "max_drawdown_max": 0.10,
            "slippage_vs_model_max": 2.0,
        }
    )
    abort: dict[str, Any] = field(
        default_factory=lambda: {
            "kill_switch": "stop all",
            "futility_after_trades": 20,
            "futility_profit_factor": 0.7,
            "data_stale_bars": 2,
            "any_change": "ends the test",
        }
    )

    def canonical(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    def candidates(self) -> list[str]:
        return sorted({s.candidate for s in self.streams})

    def validate(self) -> None:
        if self.leverage != 1.0:
            raise SpecError("this test runs at 1x only")
        if not 0 < self.risk_per_trade <= MAX_RISK_PER_TRADE:
            raise SpecError("risk per trade must be at most 0.5 %")
        if self.max_drawdown > 0.10 or self.daily_loss_limit > 0.02:
            raise SpecError("loss limits may not be looser than the risk manager's defaults")
        ids = [s.id for s in self.streams]
        if len(ids) != len(set(ids)):
            raise SpecError("duplicate stream")
        for s in self.streams:
            if s.interval_ms not in SUPPORTED:
                raise SpecError(f"unsupported interval for {s.id}")
            if s.strategy not in ("donchian_breakout", "sma_cross"):
                raise SpecError(f"unknown strategy for {s.id}")
            if s.symbol not in INSTRUMENTS:
                raise SpecError(f"unknown market {s.symbol}")


#: Markets admitted for THIS test (by the objective liquidity/history rule).
INSTRUMENTS: Final[dict[str, Instrument]] = {
    i.symbol: i for i in (BTC_USD, ETH_USD, SOL_USD, XRP_USD, BNB_USD)
}

_A = {"entry_n": 20, "exit_n": 20, "stop_atr": 2.0, "tp_atr": 6.0, "atr_n": 14, "allow_short": True}
_B = {"fast": 20, "slow": 100, "stop_atr": 2.0, "tp_atr": 4.0, "atr_n": 14, "allow_short": True}
_SLIP = {"BTC": 5.0, "ETH": 5.0, "SOL": 5.0, "XRP": 8.0, "BNB": 8.0}


def _stream(
    candidate: str, base: str, interval_ms: int, strategy: str, params: dict[str, Any]
) -> StreamSpec:
    return StreamSpec(
        candidate,
        f"{base}-USD",
        interval_ms,
        strategy,
        dict(params),
        _SLIP[base],
        2.0,
        f"binance:perp:{base}USDT",
        f"okx:perp:{base}-USDT-SWAP",
    )


DEFAULT_SPEC: Final = PaperSpec(
    name="forward-paper-test-1",
    version=1,
    streams=(
        *(
            _stream("A", b, D1, "donchian_breakout", _A)
            for b in ("BTC", "ETH", "SOL", "XRP", "BNB")
        ),
        _stream("B", "BTC", H4, "sma_cross", _B),
    ),
)


__all__ = [
    "DEFAULT_SPEC",
    "INSTRUMENTS",
    "MAX_RISK_PER_TRADE",
    "PaperSpec",
    "SpecError",
    "StreamSpec",
]
