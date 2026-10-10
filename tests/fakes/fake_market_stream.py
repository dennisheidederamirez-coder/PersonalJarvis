"""Fake Binance USD-M combined market stream for the recorder tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable


def depth(sym: str, ev: int) -> str:
    return json.dumps(
        {
            "stream": f"{sym.lower()}@depth20@500ms",
            "data": {
                "e": "depthUpdate",
                "E": ev,
                "T": ev - 2,
                "s": sym,
                "U": 1,
                "u": ev,
                "pu": ev - 1,
                "b": [["100.0", "1.5"], ["99.9", "2.0"]],
                "a": [["100.1", "0.7"], ["100.2", "3.0"]],
            },
        }
    )


def agg(sym: str, ts: int, i: int) -> str:
    return json.dumps(
        {
            "stream": f"{sym.lower()}@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": ts,
                "s": sym,
                "a": i,
                "p": "100.05",
                "q": "0.01",
                "f": i,
                "l": i,
                "T": ts,
                "m": i % 2 == 0,
            },
        }
    )


def liquidation(sym: str, ts: int) -> str:
    return json.dumps(
        {
            "stream": f"{sym.lower()}@forceOrder",
            "data": {
                "e": "forceOrder",
                "E": ts,
                "o": {
                    "s": sym,
                    "S": "SELL",
                    "o": "LIMIT",
                    "f": "IOC",
                    "q": "0.5",
                    "p": "99.0",
                    "ap": "99.2",
                    "X": "FILLED",
                    "l": "0.5",
                    "z": "0.5",
                    "T": ts,
                },
            },
        }
    )


class ScriptedConn:
    """Yields messages; ``on_recv`` runs before each (e.g. to advance a clock)."""

    def __init__(
        self,
        make: Callable[[int], str],
        on_recv: Callable[[], None] | None = None,
        hang_after: int | None = None,
    ) -> None:
        self.make, self.on_recv, self.hang_after, self.n = make, on_recv, hang_after, 0

    async def recv(self) -> str:
        if self.hang_after is not None and self.n >= self.hang_after:
            await asyncio.Event().wait()  # a silent socket
        if self.on_recv:
            self.on_recv()
        self.n += 1
        return self.make(self.n)


class Connector:
    """Hands out connections per endpoint. ``public`` and ``market`` are
    lists used in order for that endpoint's successive connects; a single
    list given positionally serves both. ``None`` = an open that never
    completes, an Exception = a failed connect."""

    def __init__(
        self,
        conns: list[ScriptedConn | Exception | None] | None = None,
        *,
        public: list[ScriptedConn | Exception | None] | None = None,
        market: list[ScriptedConn | Exception | None] | None = None,
    ) -> None:
        self.lists = {"public": public or conns or [], "market": market or conns or []}
        self.urls: list[str] = []
        self.calls = {"public": 0, "market": 0}

    @contextlib.asynccontextmanager
    async def __call__(self, url: str) -> AsyncIterator[ScriptedConn]:
        self.urls.append(url)
        cat = "market" if "/market/" in url else "public"
        items = self.lists[cat]
        item = items[min(self.calls[cat], len(items) - 1)]
        self.calls[cat] += 1
        if item is None:
            await asyncio.Event().wait()
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, ScriptedConn)
        yield item
