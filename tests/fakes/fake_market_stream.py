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
    def __init__(self, conns: list[ScriptedConn | Exception | None]) -> None:
        self.conns, self.urls = conns, []  # None = an open that never completes

    @contextlib.asynccontextmanager
    async def __call__(self, url: str) -> AsyncIterator[ScriptedConn]:
        self.urls.append(url)
        item = self.conns[min(len(self.urls) - 1, len(self.conns) - 1)]
        if item is None:
            await asyncio.Event().wait()
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, ScriptedConn)
        yield item
