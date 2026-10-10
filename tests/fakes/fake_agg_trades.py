"""A Binance USD-M ``aggTrades`` endpoint over a fixed, generated trade list."""

from __future__ import annotations

import httpx


class FakeAggTrades:
    def __init__(self, trades: list[tuple[int, float, float, bool]]) -> None:
        # (ts, price, qty, buyer_is_maker), ids are list positions
        self.trades = trades
        self.calls: list[dict[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        self.calls.append(q)
        assert request.url.path == "/fapi/v1/aggTrades"
        assert "signature" not in q and "X-MBX-APIKEY" not in request.headers  # public only
        limit = int(q.get("limit", "500"))
        if "fromId" in q:
            start = int(q["fromId"])
            picked = list(range(start, min(start + limit, len(self.trades))))
        else:
            lo, hi = int(q["startTime"]), int(q["endTime"])
            assert hi - lo < 3_600_000
            picked = [i for i, t in enumerate(self.trades) if lo <= t[0] <= hi][:limit]
        return httpx.Response(
            200,
            json=[
                {
                    "a": i,
                    "p": str(self.trades[i][1]),
                    "q": str(self.trades[i][2]),
                    "T": self.trades[i][0],
                    "m": self.trades[i][3],
                }
                for i in picked
            ],
        )
