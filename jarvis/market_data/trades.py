"""Small, time-boxed public trade samples (Binance USD-M ``aggTrades``).

For calibrating execution costs only, never a trade history: each sample
is a window shorter than one hour, and a hard request budget aborts the run
before it can grow. No API key is used or accepted. Rows are stored
locally in their own table, next to (never mixed with) the bars.

An aggregated trade merges fills of one taker order at one price, which is
what a market order would have met. ``m`` ("buyer is maker") gives the
aggressor: ``m == False`` means a market buy.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from jarvis.market_data.client import MarketDataError, PoliteClient
from jarvis.trading.scalping.tape import TradeTape

AGG_TRADES_URL: Final = "https://fapi.binance.com/fapi/v1/aggTrades"
PAGE: Final = 1000
WEIGHT_PER_REQUEST: Final = 20  # measured via X-MBX-USED-WEIGHT-1M, 2026-10
MAX_WINDOW_MS: Final = 3_600_000 - 1  # Binance: start-end span must be under one hour
BYTES_PER_TRADE: Final = 60


class BudgetExceeded(RuntimeError):
    """The request budget for the sample run is used up."""


@dataclass(frozen=True, slots=True)
class Window:
    symbol: str  # e.g. "BTCUSDT"
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not 0 < self.end_ms - self.start_ms <= MAX_WINDOW_MS:
            raise ValueError("a sample window must be shorter than one hour")


def plan(windows: Sequence[Window], trades_per_hour: dict[str, float]) -> dict[str, float]:
    """Expected requests, weight and storage, from a measured trade rate."""
    requests = 0
    trades = 0.0
    for w in windows:
        n = trades_per_hour[w.symbol] * (w.end_ms - w.start_ms) / 3_600_000
        trades += n
        requests += max(1, math.ceil(n / PAGE))
    return {
        "windows": len(windows),
        "requests": requests,
        "weight": requests * WEIGHT_PER_REQUEST,
        "trades": round(trades),
        "mb": round(trades * BYTES_PER_TRADE / 1e6, 1),
    }


async def fetch_window(
    client: PoliteClient, w: Window, *, budget: int, partial_ok: bool = False
) -> tuple[TradeTape, bool]:
    """All aggregated trades in ``[start, end)``: the first page by time, the
    rest by trade id. Returns ``(tape, complete)``. Once ``client.requests``
    reaches ``budget`` (the run-wide cap) it raises BudgetExceeded — or, with
    ``partial_ok``, returns the trades so far with ``complete=False``.

    Binance serves time-window queries only for the most recent two days
    (error -4166 otherwise), so samples are necessarily recent."""
    rows: list[tuple[int, float, float, bool]] = []
    params: dict[str, object] = {
        "symbol": w.symbol,
        "startTime": w.start_ms,
        "endTime": w.end_ms - 1,
        "limit": PAGE,
    }
    while True:
        if client.requests >= budget:
            if partial_ok:
                return TradeTape.from_rows(f"binance:perp:{w.symbol}", rows), False
            raise BudgetExceeded(f"request budget of {budget} reached")
        page = await client.get_json(AGG_TRADES_URL, params)
        if not isinstance(page, list):
            raise MarketDataError(client.venue, 200, "unexpected payload")
        done = len(page) < PAGE
        for r in page:
            ts = int(r["T"])
            if ts >= w.end_ms:
                done = True
                break
            if ts >= w.start_ms:
                rows.append((ts, float(r["p"]), float(r["q"]), not bool(r["m"])))
        if done or not page:
            break
        params = {"symbol": w.symbol, "fromId": int(page[-1]["a"]) + 1, "limit": PAGE}
    return TradeTape.from_rows(f"binance:perp:{w.symbol}", rows), True


_SCHEMA: Final = """CREATE TABLE IF NOT EXISTS md_trades (
    source TEXT NOT NULL, ts INTEGER NOT NULL, seq INTEGER NOT NULL,
    price REAL NOT NULL, qty REAL NOT NULL, buyer_aggressor INTEGER NOT NULL,
    PRIMARY KEY (source, ts, seq)
)"""


class TradeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, tape: TradeTape) -> int:
        rows = [
            (
                tape.source,
                int(tape.ts[k]),
                k,
                float(tape.price[k]),
                float(tape.qty[k]),
                int(bool(tape.buyer_aggressor[k])),
            )
            for k in range(len(tape))
        ]
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM md_trades WHERE source = ? AND ts >= ? AND ts <= ?",
                (tape.source, int(tape.ts[0]) if rows else 0, int(tape.ts[-1]) if rows else -1),
            )
            conn.executemany("INSERT INTO md_trades VALUES (?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def load(self, source: str, start_ms: int, end_ms: int) -> TradeTape:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, price, qty, buyer_aggressor FROM md_trades WHERE source = ? "
                "AND ts >= ? AND ts < ? ORDER BY ts, seq",
                (source, start_ms, end_ms),
            ).fetchall()
        return TradeTape.from_rows(
            source, [(int(t), float(p), float(q), bool(b)) for t, p, q, b in rows]
        )


__all__ = ["AGG_TRADES_URL", "BudgetExceeded", "TradeStore", "Window", "fetch_window", "plan"]
