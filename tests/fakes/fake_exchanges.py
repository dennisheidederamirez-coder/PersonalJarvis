"""A fake of the four venues' public market-data endpoints for httpx.MockTransport.

Prices are a deterministic function of time, so every venue serves the "same"
market (with a small per-venue offset), and each endpoint mimics its venue's
shape: page size, newest-first vs. oldest-first order, string numbers, OKX's
``confirm`` flag for the open bar, Coinbase's USD quote and seconds.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

HOUR = 3_600_000

OFFSET = {"bybit": 1.0, "okx": 1.0002, "binance": 0.9999, "coinbase": 1.0005}


def price(ts: int, venue: str) -> float:
    return 30_000 * (1 + 0.05 * math.sin(ts / (50 * HOUR))) * OFFSET[venue]


def _bar(ts: int, venue: str) -> tuple[float, float, float, float, float]:
    o, c = price(ts, venue), price(ts + HOUR, venue)
    return o, max(o, c) * 1.001, min(o, c) * 0.999, c, 100.0 + (ts // HOUR) % 7


class FakeExchanges:
    def __init__(self, now_ms: int, *, listed_from: int = 0) -> None:
        self.now_ms = now_ms
        self.listed_from = listed_from
        self.calls: list[str] = []
        self.fail_next: list[int] = []  # status codes to answer before succeeding
        self.retry_after: str | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _hours(self, start: int, end: int) -> list[int]:
        first = max(start, self.listed_from)
        first -= first % HOUR
        if first < start:
            first += HOUR
        return [t for t in range(first, end + 1, HOUR) if t <= self.now_ms]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.fail_next:
            code = self.fail_next.pop(0)
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(code, json={"msg": "secret provider detail"}, headers=headers)
        q = dict(request.url.params)
        host, path = request.url.host, request.url.path
        route: dict[str, Callable[[dict[str, str]], Any]] = {
            "/v5/market/kline": self._bybit_kline,
            "/v5/market/funding/history": self._bybit_funding,
            "/v5/market/open-interest": self._bybit_oi,
            "/api/v5/market/history-candles": self._okx_candles,
            "/api/v5/public/funding-rate-history": self._okx_funding,
            "/api/v3/klines": lambda p: self._binance_klines(p, "binance"),
            "/fapi/v1/klines": lambda p: self._binance_klines(p, "binance"),
            "/fapi/v1/fundingRate": self._binance_funding,
            "/futures/data/openInterestHist": self._binance_oi,
        }
        if host == "api.exchange.coinbase.com":
            return httpx.Response(200, json=self._coinbase(q))
        return httpx.Response(200, json=route[path](q))

    # --------------------------------------------------------------- bybit
    def _bybit_kline(self, q: dict[str, str]) -> Any:
        ts = self._hours(int(q["start"]), int(q["end"]))[: int(q["limit"])]
        rows = [
            [str(t), *[f"{v:.2f}" for v in _bar(t, "bybit")[:4]], str(_bar(t, "bybit")[4]), "0"]
            for t in ts
        ]
        return {"retCode": 0, "result": {"list": rows[::-1]}}

    def _bybit_funding(self, q: dict[str, str]) -> Any:
        start, end = int(q["startTime"]), int(q["endTime"])
        ts = [t for t in self._hours(start, end) if t % (8 * HOUR) == 0][::-1][: int(q["limit"])]
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {"symbol": q["symbol"], "fundingRate": "0.0001", "fundingRateTimestamp": str(t)}
                    for t in ts
                ]
            },
        }

    def _bybit_oi(self, q: dict[str, str]) -> Any:
        ts = self._hours(int(q["startTime"]), int(q["endTime"]))[::-1]
        page = int(q.get("cursor") or 0)
        chunk = ts[page * 200 : (page + 1) * 200]
        nxt = str(page + 1) if (page + 1) * 200 < len(ts) else ""
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {"openInterest": f"{50_000 + t // HOUR % 100}", "timestamp": str(t)}
                    for t in chunk
                ],
                "nextPageCursor": nxt,
            },
        }

    # ----------------------------------------------------------------- okx
    def _okx_candles(self, q: dict[str, str]) -> Any:
        after, before = int(q["after"]), int(q["before"])
        ts = [t for t in self._hours(before + 1, after - 1)][-int(q["limit"]) :]
        rows = []
        for t in ts[::-1]:
            o, h, lo, c, v = _bar(t, "okx")
            confirm = "0" if t + HOUR > self.now_ms else "1"
            rows.append(
                [str(t), f"{o}", f"{h}", f"{lo}", f"{c}", str(v * 100), str(v), "0", confirm]
            )
        return {"code": "0", "data": rows}

    def _okx_funding(self, q: dict[str, str]) -> Any:
        after = int(q["after"])
        ts = [t for t in self._hours(0, after - 1) if t % (8 * HOUR) == 0][::-1][: int(q["limit"])]
        return {
            "code": "0",
            "data": [
                {
                    "instId": q["instId"],
                    "fundingRate": "0.0002",
                    "realizedRate": "0.00015",
                    "fundingTime": str(t),
                }
                for t in ts
            ],
        }

    # ------------------------------------------------------------- binance
    def _binance_klines(self, q: dict[str, str], venue: str) -> Any:
        ts = self._hours(int(q["startTime"]), int(q["endTime"]))[: int(q["limit"])]
        out = []
        for t in ts:
            o, h, lo, c, v = _bar(t, venue)
            out.append(
                [
                    t,
                    str(o),
                    str(h),
                    str(lo),
                    str(c),
                    str(v),
                    t + HOUR - 1,
                    "0",
                    10,
                    str(v * 0.6),
                    "0",
                    "0",
                ]
            )
        return out

    def _binance_funding(self, q: dict[str, str]) -> Any:
        ts = [
            t for t in self._hours(int(q["startTime"]), int(q["endTime"])) if t % (8 * HOUR) == 0
        ][: int(q["limit"])]
        return [{"symbol": q["symbol"], "fundingTime": t, "fundingRate": "0.0001"} for t in ts]

    def _binance_oi(self, q: dict[str, str]) -> Any:
        ts = self._hours(int(q["startTime"]), int(q["endTime"]))[: int(q["limit"])]
        return [{"symbol": q["symbol"], "sumOpenInterest": "70000", "timestamp": t} for t in ts]

    # ------------------------------------------------------------ coinbase
    def _coinbase(self, q: dict[str, str]) -> Any:
        start = int(datetime.fromisoformat(q["start"]).timestamp() * 1000)
        end = int(datetime.fromisoformat(q["end"]).timestamp() * 1000)
        ts = self._hours(start, end)[:300]
        rows = []
        for t in ts[::-1]:
            o, h, lo, c, v = _bar(t, "coinbase")
            rows.append([t // 1000, lo, h, o, c, v])
        return rows
