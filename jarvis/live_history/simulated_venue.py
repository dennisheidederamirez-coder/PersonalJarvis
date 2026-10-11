"""A simulated venue: the documented read endpoints over synthetic, deterministic data.

Used by the tests and by ``python -m jarvis.live_history sync --simulated``;
it is the ONLY server the phase-2 client can reach. Nothing here is real
account data: positions, fills, orders and the balance are generated from a
seed, and they are internally consistent (a position's fee and realized P/L
are the sums of its fills; the wallet is the start balance plus every net
result), so the reconciler has something true to check.

Behaviour copied from the venue documentation:

- envelope ``{"code": 0, "msg": "Success", "data": …}``; history lists under
  ``positionList`` / ``tradeList`` / ``orderList`` with ``total``; open
  positions and the account as plain lists; decimal strings;
- ``skip`` / ``limit`` paging (limit ≤ 100), ``startTime`` / ``endTime``
  filters, ``positionId`` / ``orderId`` look-ups;
- order history only for 90 days, cancelled orders only for 3 days;
- signature check (double SHA-256) and a 10 requests/second limit (HTTP 429).

Assumptions where the documentation is silent (stated so tests do not
pretend otherwise): history positions are filtered by their OPEN time, a bad
signature answers HTTP 200 with a non-zero code, unknown paths answer 404 and
anything but GET answers 405.
"""

from __future__ import annotations

import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

import httpx

from jarvis.live_history.venue_rest import (
    ACCOUNT,
    HISTORY_ORDERS,
    HISTORY_POSITIONS,
    HISTORY_TRADES,
    PENDING_ORDERS,
    PENDING_POSITIONS,
    Credentials,
    query_string,
    sign,
)

BASE_URL: Final = "https://simulated-venue.invalid"
#: Dummy credentials the simulator accepts; they open nothing anywhere else.
SIM_CREDENTIALS: Final = Credentials(api_key="simulated-key", secret="simulated-secret")  # noqa: S106 - simulator only
START_BALANCE: Final = Decimal("1000")

DAY_MS: Final = 86_400_000
ORDER_RETENTION_MS: Final = 90 * DAY_MS
CANCELED_RETENTION_MS: Final = 3 * DAY_MS
RATE_LIMIT_PER_S: Final = 10
BAD_SIGNATURE_CODE: Final = 10007

_PRICES: Final = {
    "BTCUSDT": Decimal("60000"),
    "ETHUSDT": Decimal("3000"),
    "SOLUSDT": Decimal("150"),
}
_FEE_RATE: Final = Decimal("0.0006")
_CENT: Final = Decimal("0.0001")


def _q(x: Decimal, step: Decimal = _CENT) -> Decimal:
    return x.quantize(step, rounding=ROUND_HALF_UP)


def _s(x: Decimal) -> str:
    text = format(x.normalize(), "f")
    return "0" if text == "-0" else text


@dataclass
class SimulatedVenue:
    now_ms: int
    seed: int = 7
    closed_positions: int = 24
    days: int = 120
    history: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    wallet: Decimal = START_BALANCE
    #: Every request as (method, path, params) — tests assert on it.
    log: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    #: HTTP statuses to answer with before serving normally (fault injection).
    fail_next: deque[int] = field(default_factory=deque)
    #: Serve one undocumented, unusable item in the next history list.
    corrupt_next: bool = False
    #: Clock for the rate limiter (seconds); tests drive it.
    clock: Callable[[], float] | None = None
    _recent: deque[float] = field(default_factory=deque)
    _ids: int = 0

    def __post_init__(self) -> None:
        rng = random.Random(self.seed)  # noqa: S311 - synthetic data, not security
        start = self.now_ms - self.days * DAY_MS
        step = (self.days - 5) * DAY_MS // max(1, self.closed_positions)
        for k in range(self.closed_positions):
            opened = start + k * step + rng.randrange(0, DAY_MS)
            closed = opened + rng.randrange(2 * 3_600_000, 3 * DAY_MS)
            self._closed_position(rng, opened, closed)
        self._open_position(rng, self.now_ms - 2 * DAY_MS)
        # Two cancelled orders: one recent (served), one older than 3 days (no longer served).
        for age in (DAY_MS, 10 * DAY_MS):
            self._order(
                "BTCUSDT",
                "BUY",
                Decimal("0.01"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                self.now_ms - age,
                status="CANCELED",
            )

    # ------------------------------------------------------------- generation

    def _next(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids:06d}"

    def _order(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        filled: Decimal,
        fee: Decimal,
        pnl: Decimal,
        ts: int,
        *,
        status: str = "FILLED",
        price: Decimal | None = None,
        tp: Decimal | None = None,
    ) -> dict[str, Any]:
        item = {
            "orderId": self._next("O"),
            "clientId": "",
            "symbol": symbol,
            "side": side,
            "orderType": "LIMIT" if price is not None else "MARKET",
            "qty": _s(qty),
            "tradeQty": _s(filled),
            "price": _s(price) if price is not None else "",
            "status": status,
            "fee": _s(fee),
            "realizedPNL": _s(pnl),
            "tpPrice": _s(tp) if tp is not None else "",
            "slPrice": "",
            "reduceOnly": side == "SELL",
            "marginMode": "ISOLATION",
            "positionMode": "ONE_WAY",
            "leverage": 1,
            "ctime": ts,
            "mtime": ts,
        }
        (self.open_orders if status in ("NEW", "PART_FILLED") else self.orders).append(item)
        return item

    def _fill(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        pnl: Decimal,
        ts: int,
        position_id: str,
    ) -> Decimal:
        fee = _q(qty * price * _FEE_RATE)
        order = self._order(symbol, side, qty, qty, fee, pnl, ts)
        self.trades.append(
            {
                "tradeId": self._next("T"),
                "orderId": order["orderId"],
                "positionId": position_id,
                "symbol": symbol,
                "side": side,
                "qty": _s(qty),
                "price": _s(price),
                "fee": _s(fee),
                "realizedPNL": _s(pnl),
                "roleType": "TAKER",
                "reduceOnly": side == "SELL",
                "clientId": "",
                "ctime": ts,
            }
        )
        return fee

    def _closed_position(self, rng: random.Random, opened: int, closed: int) -> None:
        symbol = rng.choice(sorted(_PRICES))
        side = rng.choice(["LONG", "SHORT"])
        entry = _q(_PRICES[symbol] * Decimal(rng.uniform(0.9, 1.1)), Decimal("0.01"))
        exit_ = _q(entry * Decimal(rng.uniform(0.97, 1.03)), Decimal("0.01"))
        qty = _q(Decimal(100) / entry * Decimal(rng.randint(1, 5)), Decimal("0.001"))
        pid = self._next("P")
        half = _q(qty / 2, Decimal("0.001"))
        sign_ = 1 if side == "LONG" else -1
        legs = [(half, (opened + closed) // 2), (qty - half, closed)]
        open_side, close_side = ("BUY", "SELL") if side == "LONG" else ("SELL", "BUY")
        fee = self._fill(symbol, open_side, qty, entry, Decimal(0), opened, pid)
        realized = Decimal(0)
        for leg_qty, ts in legs:
            pnl = _q((exit_ - entry) * leg_qty * sign_)
            fee += self._fill(symbol, close_side, leg_qty, exit_, pnl, ts, pid)
            realized += pnl
        funding = _q(Decimal(rng.uniform(-0.5, 0.2)))
        self.history.append(
            {
                "positionId": pid,
                "symbol": symbol,
                "maxQty": _s(qty),
                "entryPrice": _s(entry),
                "closePrice": _s(exit_),
                "liqQty": "0",
                "side": side,
                "positionMode": "ONE_WAY",
                "marginMode": "ISOLATION",
                "leverage": 1,
                "fee": _s(fee),
                "funding": _s(funding),
                "realizedPNL": _s(realized),
                "liqPrice": "0",
                "ctime": opened,
                "mtime": closed,
            }
        )
        self.wallet += realized - fee + funding

    def _open_position(self, rng: random.Random, opened: int) -> None:
        symbol = "BTCUSDT"
        entry = _q(_PRICES[symbol] * Decimal(rng.uniform(0.98, 1.02)), Decimal("0.01"))
        qty = Decimal("0.005")
        pid = self._next("P")
        fee = self._fill(symbol, "BUY", qty, entry, Decimal(0), opened, pid)
        funding = Decimal("-0.0300")
        self.wallet += -fee + funding
        self.pending.append(
            {
                "positionId": pid,
                "symbol": symbol,
                "qty": _s(qty),
                "entryValue": _s(_q(qty * entry)),
                "side": "LONG",
                "positionMode": "ONE_WAY",
                "marginMode": "ISOLATION",
                "leverage": 1,
                "fee": _s(fee),
                "funding": _s(funding),
                "realizedPNL": "0",
                "margin": _s(_q(qty * entry)),
                "unrealizedPNL": "1.25",
                "liqPrice": "0",
                "marginRate": "0.01",
                "avgOpenPrice": _s(entry),
                "ctime": opened,
                "mtime": opened,
            }
        )
        self._order(
            symbol,
            "SELL",
            qty,
            Decimal(0),
            Decimal(0),
            Decimal(0),
            opened,
            status="NEW",
            price=_q(entry * Decimal("1.05"), Decimal("0.01")),
            tp=None,
        )

    def close_open_position(self, at_ms: int, exit_price: Decimal) -> str:
        """Close the open position at ``at_ms`` (it keeps its id and its OPEN time)."""
        pos = self.pending.pop(0)
        pid = str(pos["positionId"])
        qty, entry = Decimal(pos["qty"]), Decimal(pos["avgOpenPrice"])
        for o in list(self.open_orders):
            if o["symbol"] == pos["symbol"]:
                self.open_orders.remove(o)
                o.update(status="FILLED", tradeQty=o["qty"], mtime=at_ms)
                self.orders.append(o)
        pnl = _q((exit_price - entry) * qty)
        fee = Decimal(pos["fee"]) + self._fill(
            str(pos["symbol"]), "SELL", qty, exit_price, pnl, at_ms, pid
        )
        funding = Decimal(pos["funding"])
        self.wallet += pnl - (fee - Decimal(pos["fee"]))
        self.history.append(
            {
                "positionId": pid,
                "symbol": pos["symbol"],
                "maxQty": pos["qty"],
                "entryPrice": pos["avgOpenPrice"],
                "closePrice": _s(exit_price),
                "liqQty": "0",
                "side": "LONG",
                "positionMode": "ONE_WAY",
                "marginMode": "ISOLATION",
                "leverage": 1,
                "fee": _s(fee),
                "funding": _s(funding),
                "realizedPNL": _s(pnl),
                "liqPrice": "0",
                "ctime": pos["ctime"],
                "mtime": at_ms,
            }
        )
        return pid

    # ----------------------------------------------------------------- serving

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = {k: v for k, v in request.url.params.items()}
        self.log.append((request.method, request.url.path, params))
        if request.method != "GET":
            return httpx.Response(405)
        if self.fail_next:
            return httpx.Response(self.fail_next.popleft())
        if self.clock is not None:
            t = self.clock()
            while self._recent and t - self._recent[0] >= 1.0:
                self._recent.popleft()
            if len(self._recent) >= RATE_LIMIT_PER_S:
                return httpx.Response(429)
            self._recent.append(t)
        expected = sign(
            SIM_CREDENTIALS,
            nonce=request.headers.get("nonce", ""),
            timestamp=request.headers.get("timestamp", ""),
            query=query_string(params),
        )
        if (
            request.headers.get("api-key") != SIM_CREDENTIALS.api_key
            or request.headers.get("sign") != expected
        ):
            return httpx.Response(
                200,
                json={
                    "code": BAD_SIGNATURE_CODE,
                    "msg": "Signature error: secret=simulated-secret",
                },
            )
        route = {
            ACCOUNT: self._account,
            PENDING_POSITIONS: lambda p: list(self.pending),
            PENDING_ORDERS: lambda p: self._page(self.open_orders, p, "orderList"),
            HISTORY_POSITIONS: self._history_positions,
            HISTORY_TRADES: lambda p: self._page(self._window(self.trades, p), p, "tradeList"),
            HISTORY_ORDERS: self._history_orders,
        }.get(request.url.path)
        if route is None:
            return httpx.Response(404)
        return httpx.Response(200, json={"code": 0, "msg": "Success", "data": route(params)})

    def _account(self, params: dict[str, str]) -> list[dict[str, Any]]:
        margin = sum((Decimal(p["margin"]) for p in self.pending), Decimal(0))
        return [
            {
                "marginCoin": params.get("marginCoin", "USDT"),
                "available": _s(self.wallet - margin),
                "frozen": "0",
                "margin": _s(margin),
                "transfer": _s(self.wallet - margin),
                "positionMode": "ONE_WAY",
                "crossUnrealizedPNL": "0",
                "isolationUnrealizedPNL": _s(
                    sum((Decimal(p["unrealizedPNL"]) for p in self.pending), Decimal(0))
                ),
                "bonus": "0",
            }
        ]

    def _window(self, items: list[dict[str, Any]], params: dict[str, str]) -> list[dict[str, Any]]:
        lo = int(params.get("startTime", "0"))
        hi = int(params.get("endTime", str(2**62)))
        return [i for i in items if lo <= int(i["ctime"]) < hi]

    def _page(
        self, items: list[dict[str, Any]], params: dict[str, str], key: str
    ) -> dict[str, Any]:
        skip = int(params.get("skip", "0"))
        limit = min(100, int(params.get("limit", "10")))
        ordered = sorted(
            items,
            key=lambda i: (
                -int(i["ctime"]),
                str(i.get("tradeId") or i.get("orderId") or i.get("positionId")),
            ),
        )
        return {key: ordered[skip : skip + limit], "total": len(ordered)}

    def _history_positions(self, params: dict[str, str]) -> dict[str, Any]:
        if "positionId" in params:
            return self._page(
                [p for p in self.history if p["positionId"] == params["positionId"]],
                params,
                "positionList",
            )
        page = self._page(self._window(self.history, params), params, "positionList")
        if self.corrupt_next and page["positionList"]:
            self.corrupt_next = False
            page["positionList"] = [*page["positionList"], {"positionId": "", "symbol": "BTCUSDT"}]
        return page

    def _history_orders(self, params: dict[str, str]) -> dict[str, Any]:
        def served(o: dict[str, Any]) -> bool:
            age = self.now_ms - int(o["ctime"])
            return age < (
                CANCELED_RETENTION_MS if o["status"] == "CANCELED" else ORDER_RETENTION_MS
            )

        pool = [o for o in self.orders if served(o)]
        if "orderId" in params:
            return self._page(
                [o for o in pool if o["orderId"] == params["orderId"]], params, "orderList"
            )
        return self._page(self._window(pool, params), params, "orderList")


__all__ = ["BASE_URL", "SIM_CREDENTIALS", "START_BALANCE", "SimulatedVenue"]
