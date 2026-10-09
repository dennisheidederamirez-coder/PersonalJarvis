"""Venue adapters: OHLCV bars, funding and open interest from public endpoints.

Each adapter pages forward through ``[start_ms, end_ms)`` in windows of its
maximum page size, drops the still-open bar, de-duplicates, and returns one
``BarSeries`` whose ``source`` reads ``<venue>:<kind>:<venue symbol>``.
Endpoints and page sizes follow the venues' public documentation (2026-10);
response shapes are pinned by recorded fixtures in the tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from jarvis.market_data.client import MarketDataError, PoliteClient
from jarvis.trading.data import DAY_MS, HOUR_MS, BarSeries, make_series
from jarvis.trading.instruments import Instrument

MINUTE_MS: Final = 60_000


class Kind(StrEnum):
    SPOT = "spot"
    PERP = "perp"  # linear USDT-margined perpetual


@dataclass(frozen=True, slots=True)
class FundingPoint:
    ts_ms: int
    rate: float  # per funding interval (e.g. 8 h), as a fraction


@dataclass(frozen=True, slots=True)
class OpenInterestPoint:
    ts_ms: int
    value: float
    unit: str  # "base" (contracts in coins) or "quote" (USD value)


Row = tuple[int, float, float, float, float, float]


def _now_ms() -> int:
    return int(time.time() * 1000)


class Adapter:
    name: str = ""
    kinds: frozenset[Kind] = frozenset()
    page_bars: int = 0

    def __init__(self, client: PoliteClient, *, now_ms: Callable[[], int] = _now_ms) -> None:
        self.client = client
        self._now_ms = now_ms

    # ----------------------------------------------------------- public api

    def symbol(self, instrument: Instrument, kind: Kind) -> str:
        raise NotImplementedError

    async def bars(
        self, instrument: Instrument, kind: Kind, interval_ms: int, start_ms: int, end_ms: int
    ) -> BarSeries:
        if kind not in self.kinds:
            raise MarketDataError(self.name, None, f"no {kind} data")
        sym = self.symbol(instrument, kind)
        closed_before = min(end_ms, self._now_ms())
        rows: dict[int, Row] = {}
        taker: dict[int, float] = {}
        cursor = start_ms - start_ms % interval_ms
        while cursor < end_ms:
            window_end = min(end_ms, cursor + self.page_bars * interval_ms)
            page = await self._page(kind, sym, interval_ms, cursor, window_end)
            for row, buy in page:
                ts = row[0]
                if start_ms <= ts < window_end and ts + interval_ms <= closed_before:
                    rows[ts] = row
                    if buy is not None:
                        taker[ts] = buy
            cursor = window_end
        ordered = [rows[t] for t in sorted(rows)]
        buys = [taker[t] for t in sorted(rows)] if taker and len(taker) == len(rows) else None
        return make_series(
            instrument,
            interval_ms,
            ordered,
            source=f"{self.name}:{kind}:{sym}",
            retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            taker_buy=buys,
        )

    async def funding(
        self, instrument: Instrument, start_ms: int, end_ms: int
    ) -> list[FundingPoint]:
        raise MarketDataError(self.name, None, "no funding history")

    async def open_interest(
        self, instrument: Instrument, start_ms: int, end_ms: int
    ) -> list[OpenInterestPoint]:
        raise MarketDataError(self.name, None, "no open-interest history")

    # ------------------------------------------------------------ internals

    async def _page(
        self, kind: Kind, sym: str, interval_ms: int, start_ms: int, end_ms: int
    ) -> list[tuple[Row, float | None]]:
        raise NotImplementedError

    def _check(self, ok: bool, code: Any) -> None:
        if not ok:
            raise MarketDataError(self.name, 200, str(code))


def _f(x: Any) -> float:
    return float(x)


# ----------------------------------------------------------------------- Bybit


class BybitAdapter(Adapter):
    name = "bybit"
    kinds = frozenset({Kind.SPOT, Kind.PERP})
    page_bars = 1000
    BASE: Final = "https://api.bybit.com"
    _INTERVALS: Final = {
        MINUTE_MS: "1",
        5 * MINUTE_MS: "5",
        15 * MINUTE_MS: "15",
        HOUR_MS: "60",
        4 * HOUR_MS: "240",
        DAY_MS: "D",
    }

    def symbol(self, instrument: Instrument, kind: Kind) -> str:
        return f"{instrument.base}USDT"

    def _category(self, kind: Kind) -> str:
        return "spot" if kind is Kind.SPOT else "linear"

    async def _page(
        self, kind: Kind, sym: str, interval_ms: int, start_ms: int, end_ms: int
    ) -> list[tuple[Row, float | None]]:
        data = await self.client.get_json(
            f"{self.BASE}/v5/market/kline",
            {
                "category": self._category(kind),
                "symbol": sym,
                "interval": self._INTERVALS[interval_ms],
                "start": start_ms,
                "end": end_ms - 1,
                "limit": self.page_bars,
            },
        )
        self._check(data.get("retCode") == 0, data.get("retCode"))
        return [
            ((int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5])), None)
            for r in data["result"]["list"]
        ]

    async def funding(
        self, instrument: Instrument, start_ms: int, end_ms: int
    ) -> list[FundingPoint]:
        out: dict[int, FundingPoint] = {}
        cursor_end = end_ms
        while cursor_end > start_ms:
            data = await self.client.get_json(
                f"{self.BASE}/v5/market/funding/history",
                {
                    "category": "linear",
                    "symbol": self.symbol(instrument, Kind.PERP),
                    "startTime": start_ms,
                    "endTime": cursor_end - 1,
                    "limit": 200,
                },
            )
            self._check(data.get("retCode") == 0, data.get("retCode"))
            page = data["result"]["list"]
            for r in page:
                ts = int(r["fundingRateTimestamp"])
                if start_ms <= ts < end_ms:
                    out[ts] = FundingPoint(ts, _f(r["fundingRate"]))
            if len(page) < 200:
                break
            cursor_end = min(int(r["fundingRateTimestamp"]) for r in page)
        return [out[t] for t in sorted(out)]

    async def open_interest(
        self, instrument: Instrument, start_ms: int, end_ms: int
    ) -> list[OpenInterestPoint]:
        out: dict[int, OpenInterestPoint] = {}
        cursor = ""
        for _ in range(10_000):
            data = await self.client.get_json(
                f"{self.BASE}/v5/market/open-interest",
                {
                    "category": "linear",
                    "symbol": self.symbol(instrument, Kind.PERP),
                    "intervalTime": "1h",
                    "startTime": start_ms,
                    "endTime": end_ms - 1,
                    "limit": 200,
                    "cursor": cursor or None,
                },
            )
            self._check(data.get("retCode") == 0, data.get("retCode"))
            for r in data["result"]["list"]:
                ts = int(r["timestamp"])
                out[ts] = OpenInterestPoint(ts, _f(r["openInterest"]), "base")
            cursor = data["result"].get("nextPageCursor") or ""
            if not cursor:
                break
        return [out[t] for t in sorted(out)]


# ------------------------------------------------------------------------- OKX


class OkxAdapter(Adapter):
    name = "okx"
    kinds = frozenset({Kind.SPOT, Kind.PERP})
    page_bars = 100
    BASE: Final = "https://www.okx.com"
    _INTERVALS: Final = {
        MINUTE_MS: "1m",
        5 * MINUTE_MS: "5m",
        15 * MINUTE_MS: "15m",
        HOUR_MS: "1H",
        4 * HOUR_MS: "4H",
        DAY_MS: "1Dutc",
    }

    def symbol(self, instrument: Instrument, kind: Kind) -> str:
        return f"{instrument.base}-USDT" + ("-SWAP" if kind is Kind.PERP else "")

    async def _page(
        self, kind: Kind, sym: str, interval_ms: int, start_ms: int, end_ms: int
    ) -> list[tuple[Row, float | None]]:
        data = await self.client.get_json(
            f"{self.BASE}/api/v5/market/history-candles",
            {
                "instId": sym,
                "bar": self._INTERVALS[interval_ms],
                "after": end_ms,
                "before": start_ms - 1,
                "limit": self.page_bars,
            },
        )
        self._check(data.get("code") == "0", data.get("code"))
        out: list[tuple[Row, float | None]] = []
        for r in data["data"]:
            if len(r) > 8 and r[8] != "1":
                continue  # not confirmed: the bar is still open
            # spot: vol is in base coin; swap: vol is contracts, volCcy is base coin
            volume = _f(r[6]) if kind is Kind.PERP else _f(r[5])
            out.append(((int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), volume), None))
        return out

    async def funding(
        self, instrument: Instrument, start_ms: int, end_ms: int
    ) -> list[FundingPoint]:
        out: dict[int, FundingPoint] = {}
        after = end_ms
        while after > start_ms:
            data = await self.client.get_json(
                f"{self.BASE}/api/v5/public/funding-rate-history",
                {
                    "instId": self.symbol(instrument, Kind.PERP),
                    "after": after,
                    "limit": 100,
                },
            )
            self._check(data.get("code") == "0", data.get("code"))
            page = data["data"]
            for r in page:
                ts = int(r["fundingTime"])
                if start_ms <= ts < end_ms:
                    out[ts] = FundingPoint(ts, _f(r.get("realizedRate") or r["fundingRate"]))
            if len(page) < 100:
                break
            after = min(int(r["fundingTime"]) for r in page)
        return [out[t] for t in sorted(out)]


# --------------------------------------------------------------------- Binance


class BinanceAdapter(Adapter):
    """Binance REST API (spot and USD-M futures). Not the bulk datasets, whose
    licence forbids live trading use (see docs/trading-market-data.md)."""

    name = "binance"
    kinds = frozenset({Kind.SPOT, Kind.PERP})
    page_bars = 1000
    SPOT: Final = "https://api.binance.com"
    FUTURES: Final = "https://fapi.binance.com"
    _INTERVALS: Final = {
        MINUTE_MS: "1m",
        5 * MINUTE_MS: "5m",
        15 * MINUTE_MS: "15m",
        HOUR_MS: "1h",
        4 * HOUR_MS: "4h",
        DAY_MS: "1d",
    }

    def symbol(self, instrument: Instrument, kind: Kind) -> str:
        return f"{instrument.base}USDT"

    async def _page(
        self, kind: Kind, sym: str, interval_ms: int, start_ms: int, end_ms: int
    ) -> list[tuple[Row, float | None]]:
        url = (
            f"{self.SPOT}/api/v3/klines" if kind is Kind.SPOT else f"{self.FUTURES}/fapi/v1/klines"
        )
        data = await self.client.get_json(
            url,
            {
                "symbol": sym,
                "interval": self._INTERVALS[interval_ms],
                "startTime": start_ms,
                "endTime": end_ms - 1,
                "limit": self.page_bars,
            },
        )
        self._check(isinstance(data, list), "unexpected payload")
        return [
            ((int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5])), _f(r[9])) for r in data
        ]

    async def funding(
        self, instrument: Instrument, start_ms: int, end_ms: int
    ) -> list[FundingPoint]:
        out: dict[int, FundingPoint] = {}
        cursor = start_ms
        while cursor < end_ms:
            data = await self.client.get_json(
                f"{self.FUTURES}/fapi/v1/fundingRate",
                {
                    "symbol": self.symbol(instrument, Kind.PERP),
                    "startTime": cursor,
                    "endTime": end_ms - 1,
                    "limit": 1000,
                },
            )
            self._check(isinstance(data, list), "unexpected payload")
            for r in data:
                ts = int(r["fundingTime"])
                out[ts] = FundingPoint(ts, _f(r["fundingRate"]))
            if len(data) < 1000:
                break
            cursor = max(int(r["fundingTime"]) for r in data) + 1
        return [out[t] for t in sorted(out)]

    async def open_interest(
        self, instrument: Instrument, start_ms: int, end_ms: int
    ) -> list[OpenInterestPoint]:
        """Binance serves only the last 30 days of open-interest history."""
        data = await self.client.get_json(
            f"{self.FUTURES}/futures/data/openInterestHist",
            {
                "symbol": self.symbol(instrument, Kind.PERP),
                "period": "1h",
                "startTime": max(start_ms, self._now_ms() - 30 * DAY_MS + HOUR_MS),
                "endTime": end_ms - 1,
                "limit": 500,
            },
        )
        self._check(isinstance(data, list), "unexpected payload")
        return sorted(
            (
                OpenInterestPoint(int(r["timestamp"]), _f(r["sumOpenInterest"]), "base")
                for r in data
            ),
            key=lambda p: p.ts_ms,
        )


# -------------------------------------------------------------------- Coinbase


class CoinbaseAdapter(Adapter):
    """Spot only, quoted in USD (not USDT) — an independent price reference."""

    name = "coinbase"
    kinds = frozenset({Kind.SPOT})
    page_bars = 300
    BASE: Final = "https://api.exchange.coinbase.com"
    _GRANULARITY: Final = {
        MINUTE_MS: 60,
        5 * MINUTE_MS: 300,
        15 * MINUTE_MS: 900,
        HOUR_MS: 3600,
        DAY_MS: 86400,
    }

    def symbol(self, instrument: Instrument, kind: Kind) -> str:
        return f"{instrument.base}-USD"

    async def _page(
        self, kind: Kind, sym: str, interval_ms: int, start_ms: int, end_ms: int
    ) -> list[tuple[Row, float | None]]:
        iso = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).isoformat()  # noqa: E731
        data = await self.client.get_json(
            f"{self.BASE}/products/{sym}/candles",
            {
                "granularity": self._GRANULARITY[interval_ms],
                "start": iso(start_ms),
                "end": iso(end_ms - interval_ms),
            },
        )
        self._check(isinstance(data, list), "unexpected payload")
        # [time_s, low, high, open, close, volume]
        return [
            ((int(r[0]) * 1000, _f(r[3]), _f(r[2]), _f(r[1]), _f(r[4]), _f(r[5])), None)
            for r in data
        ]


ADAPTERS: Final[dict[str, type[Adapter]]] = {
    a.name: a for a in (BybitAdapter, OkxAdapter, BinanceAdapter, CoinbaseAdapter)
}

#: Polite spacing per venue: well below each published public limit.
MIN_INTERVAL_S: Final[dict[str, float]] = {
    "bybit": 0.1,
    "okx": 0.25,
    "binance": 0.2,
    "coinbase": 0.35,
}


def make_adapter(name: str, **client_kw: Any) -> Adapter:
    cls = ADAPTERS[name]
    return cls(PoliteClient(name, min_interval_s=MIN_INTERVAL_S[name], **client_kw))


__all__ = [
    "ADAPTERS",
    "Adapter",
    "BinanceAdapter",
    "BybitAdapter",
    "CoinbaseAdapter",
    "FundingPoint",
    "Kind",
    "OkxAdapter",
    "OpenInterestPoint",
    "make_adapter",
]
