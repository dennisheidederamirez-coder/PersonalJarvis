"""The simulated broker: virtual capital, simulated fills, no exchange.

Costs are charged on every fill so results are never flattered:
- a taker fee on the notional of each fill;
- slippage: buys fill above, sells below the reference price;
- funding on open positions (perpetual-style: longs pay a positive rate,
  shorts receive it).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.trading.instruments import Instrument
from jarvis.trading.leverage import liquidation_price
from jarvis.trading.strategies import Side


@dataclass(frozen=True, slots=True)
class CostModel:
    fee_rate: float = 0.0005  # 0.05 % taker per fill
    slippage_bps: float = 5.0  # 0.05 % adverse per fill
    funding_rate_8h: float = 0.0001  # 0.01 % per 8 h, longs pay

    def fill_price(self, price: float, buy: bool) -> float:
        s = self.slippage_bps / 10_000
        return price * (1 + s) if buy else price * (1 - s)


@dataclass
class Position:
    client_id: str
    instrument: Instrument
    side: Side
    qty: float
    entry: float  # fill price incl. slippage
    stop: float
    take_profit: float | None
    opened_ms: int
    entry_fee: float
    entry_slippage: float
    initial_risk: float  # qty * |entry - stop| at the fill
    funding: float = 0.0
    reason: str = ""
    strategy: str = ""  # the strategy that owns the position (attribution)
    leverage: float = 1.0
    margin: float = 0.0  # isolated margin; the most an isolated position can lose
    liquidation: float | None = None

    def unrealized(self, mark: float) -> float:
        return self.side.sign * (mark - self.entry) * self.qty


@dataclass(frozen=True, slots=True)
class Fill:
    client_id: str
    symbol: str
    action: str  # open / close
    side: Side
    qty: float
    price: float
    fee: float
    slippage: float
    ts_ms: int
    reason: str


@dataclass(frozen=True, slots=True)
class Trade:
    client_id: str
    symbol: str
    side: Side
    qty: float
    entry_ms: int
    exit_ms: int
    entry: float
    exit: float
    gross: float  # on fill prices (slippage already inside)
    fees: float
    slippage: float  # informational: what slippage cost on both legs
    funding: float
    net: float
    r_multiple: float  # net / initial risk
    entry_reason: str
    exit_reason: str
    strategy: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            k: (v.value if isinstance(v, Side) else v)
            for k, v in ((f, getattr(self, f)) for f in self.__dataclass_fields__)
        }


@dataclass
class PaperBroker:
    capital: float
    costs: CostModel = field(default_factory=CostModel)
    positions: dict[str, Position] = field(default_factory=dict)
    cash: float = 0.0  # realized equity

    def __post_init__(self) -> None:
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        self.cash = self.capital

    def open(
        self,
        client_id: str,
        instrument: Instrument,
        side: Side,
        qty: float,
        price: float,
        stop: float,
        take_profit: float | None,
        ts_ms: int,
        reason: str = "",
        strategy: str = "",
        leverage: float = 1.0,
        mmr: float = 0.005,
    ) -> Fill:
        if instrument.symbol in self.positions:
            raise RuntimeError("position already open")  # the risk manager prevents this
        fill = self.costs.fill_price(price, buy=side is Side.LONG)
        fee = abs(fill * qty) * self.costs.fee_rate
        slip = abs(fill - price) * qty
        self.cash -= fee
        self.positions[instrument.symbol] = Position(
            client_id,
            instrument,
            side,
            qty,
            fill,
            stop,
            take_profit,
            ts_ms,
            fee,
            slip,
            qty * abs(fill - stop),
            reason=reason,
            strategy=strategy,
            leverage=leverage,
            margin=abs(fill * qty) / leverage,
            liquidation=(
                liquidation_price(side, fill, qty, leverage, mmr=mmr, fee_rate=self.costs.fee_rate)
                if leverage > 1
                else None
            ),
        )
        return Fill(client_id, instrument.symbol, "open", side, qty, fill, fee, slip, ts_ms, reason)

    def close(
        self, symbol: str, price: float, ts_ms: int, reason: str, client_id: str
    ) -> tuple[Fill, Trade]:
        pos = self.positions.pop(symbol)
        if reason == "liquidation":
            # Isolated margin: the venue closes at the liquidation price and keeps
            # what is left of the margin — the loss is the whole margin.
            fill = price
            gross = pos.side.sign * (fill - pos.entry) * pos.qty
            fee = max(0.0, pos.margin + gross)  # the remaining margin is forfeited
            slip = 0.0
        else:
            fill = self.costs.fill_price(price, buy=pos.side is Side.SHORT)
            fee = abs(fill * pos.qty) * self.costs.fee_rate
            slip = abs(fill - price) * pos.qty
            gross = pos.side.sign * (fill - pos.entry) * pos.qty
        self.cash += gross - fee
        fees = pos.entry_fee + fee
        net = gross - fees - pos.funding
        r = net / pos.initial_risk if pos.initial_risk > 0 else 0.0
        trade = Trade(
            pos.client_id,
            symbol,
            pos.side,
            pos.qty,
            pos.opened_ms,
            ts_ms,
            pos.entry,
            fill,
            gross,
            fees,
            pos.entry_slippage + slip,
            pos.funding,
            net,
            r,
            pos.reason,
            reason,
            pos.strategy,
        )
        return Fill(
            client_id, symbol, "close", pos.side, pos.qty, fill, fee, slip, ts_ms, reason
        ), trade

    def accrue_funding(self, symbol: str, mark: float, hours: float) -> float:
        pos = self.positions.get(symbol)
        if pos is None or hours <= 0:
            return 0.0
        amount = pos.side.sign * abs(pos.qty * mark) * self.costs.funding_rate_8h * hours / 8
        pos.funding += amount
        self.cash -= amount
        return amount

    def charge_funding(self, symbol: str, mark: float, rate: float) -> float:
        """One observed funding payment at *rate* (longs pay a positive rate)."""
        pos = self.positions.get(symbol)
        if pos is None:
            return 0.0
        amount = pos.side.sign * abs(pos.qty * mark) * rate
        pos.funding += amount
        self.cash -= amount
        return amount

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + sum(
            p.unrealized(marks.get(sym, p.entry)) for sym, p in self.positions.items()
        )


__all__ = ["CostModel", "Fill", "PaperBroker", "Position", "Trade"]
