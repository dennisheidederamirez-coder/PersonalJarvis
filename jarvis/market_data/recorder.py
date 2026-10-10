"""One-off, time-boxed recorder for PUBLIC Binance USD-M market streams.

Records, for the given symbols, over TWO connections. Since the 2026-03
endpoint split, the legacy URL delivers only the ``/public`` category:

- ``/public``: ``<sym>@depth20@500ms``, the top 20 book levels as a full
  partial-book snapshot per message, so no diff-sync state can drift;
- ``/market``: ``<sym>@aggTrade``, trades with the aggressor side;
- ``/market``: ``<sym>@forceOrder``, liquidations. Binance pushes only the
  latest one per symbol per second, so this is a lower bound, stored with
  that coverage note.

If one connection is lost for good, the whole session ends: an incomplete
recording is worse than a short one.

Everything recorded is OBSERVED market data and is stored as such. Modelled
liquidity or liquidation zones are never written here.

Safety:

- No API key is read or sent, and no order endpoint exists in this module.
- A session lasts at most ``MAX_DURATION_S`` (60 min); a longer value is
  refused.
- It stops at the FIRST of:
  1. the deadline, checked on every receive, which also times out a silent
     socket;
  2. an outer ``asyncio.timeout`` around the whole session (deadline + grace);
  3. a watchdog thread that hard-exits the process (``install_hard_stop``,
     used by the CLI) even if the event loop is wedged;
  4. a stop file, the storage cap, or too many reconnects.
- Reconnects are jittered with back-off, at most ``max_reconnects``. No
  restart happens after the process ends, and nothing schedules it.
- Logs and stored events never contain provider response bodies.

Starting it needs the owner's explicit approval: the CLI refuses to connect
without ``--owner-approved``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

log = logging.getLogger(__name__)

MAX_DURATION_S: Final = 3600.0
GRACE_S: Final = 30.0
WS_HOST: Final = "wss://fstream.binance.com"
#: stream category -> endpoint (Binance USD-M WebSocket split, 2026-03)
ENDPOINTS: Final = {
    "public": f"{WS_HOST}/public/stream?streams=",
    "market": f"{WS_HOST}/market/stream?streams=",
}
_KINDS: Final = {"public": ("depth20@500ms",), "market": ("aggTrade", "forceOrder")}
LIQUIDATION_COVERAGE: Final = "binance forceOrder: latest liquidation per symbol per 1000 ms only"
_SYMBOL = re.compile(r"^[A-Z0-9]{2,20}$")


class Conn(Protocol):
    async def recv(self) -> str | bytes: ...


Connector = Callable[[str], contextlib.AbstractAsyncContextManager[Conn]]


@dataclass(frozen=True)
class RecorderConfig:
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    duration_s: float = MAX_DURATION_S
    max_bytes: int = 200_000_000
    max_reconnects: int = 3
    open_timeout_s: float = 10.0
    idle_timeout_s: float = 30.0
    stop_file: Path | None = None
    flush_every: int = 500

    def __post_init__(self) -> None:
        if not 0 < self.duration_s <= MAX_DURATION_S:
            raise ValueError(f"duration must be in (0, {MAX_DURATION_S:.0f}] seconds")
        if not self.symbols or not all(_SYMBOL.match(s) for s in self.symbols):
            raise ValueError("symbols must be upper-case exchange symbols")

    def streams(self, category: str | None = None) -> list[str]:
        cats = [category] if category else list(_KINDS)
        return [f"{s.lower()}@{kind}" for c in cats for s in self.symbols for kind in _KINDS[c]]

    def urls(self) -> dict[str, str]:
        return {c: ENDPOINTS[c] + "/".join(self.streams(c)) for c in _KINDS}


@dataclass
class Summary:
    stop_reason: str = ""
    messages: int = 0
    bytes: int = 0
    reconnects: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    max_book_gap_ms: dict[str, int] = field(default_factory=dict)
    latency_ms_median: float | None = None


_SCHEMA: Final = (
    """CREATE TABLE IF NOT EXISTS rec_sessions (id INTEGER PRIMARY KEY, started_ms INTEGER,
        ended_ms INTEGER, symbols TEXT, streams TEXT, stop_reason TEXT, summary TEXT)""",
    """CREATE TABLE IF NOT EXISTS rec_book (session INTEGER, symbol TEXT, event_ms INTEGER,
        txn_ms INTEGER, recv_ms INTEGER, update_id INTEGER, bids TEXT, asks TEXT)""",
    """CREATE TABLE IF NOT EXISTS rec_trades (session INTEGER, symbol TEXT, agg_id INTEGER,
        ts INTEGER, price REAL, qty REAL, buyer_aggressor INTEGER, recv_ms INTEGER)""",
    """CREATE TABLE IF NOT EXISTS rec_liquidations (session INTEGER, symbol TEXT, ts INTEGER,
        side TEXT, price REAL, avg_price REAL, qty REAL, filled_qty REAL, status TEXT,
        provenance TEXT NOT NULL CHECK (provenance = 'observed'), coverage TEXT)""",
    """CREATE TABLE IF NOT EXISTS rec_events (session INTEGER, ts_ms INTEGER, kind TEXT,
        detail TEXT)""",
)


class RecordStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        for stmt in _SCHEMA:
            self._db.execute(stmt)
        self._rows: dict[str, list[tuple[Any, ...]]] = {}
        self.session = 0

    def begin(self, cfg: RecorderConfig, now_ms: int) -> int:
        cur = self._db.execute(
            "INSERT INTO rec_sessions (started_ms, symbols, streams) VALUES (?, ?, ?)",
            (now_ms, ",".join(cfg.symbols), ",".join(cfg.streams())),
        )
        self._db.commit()
        self.session = int(cur.lastrowid or 0)
        return self.session

    def add(self, table: str, row: tuple[Any, ...]) -> None:
        self._rows.setdefault(table, []).append((self.session, *row))

    def pending(self) -> int:
        return sum(len(v) for v in self._rows.values())

    def flush(self) -> None:
        for table, rows in self._rows.items():
            if rows:
                marks = ",".join("?" * len(rows[0]))
                self._db.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)  # noqa: S608 — fixed table names
        self._rows = {}
        self._db.commit()

    def size(self) -> int:
        return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*"))

    def end(self, now_ms: int, summary: Summary) -> None:
        self.flush()
        self._db.execute(
            "UPDATE rec_sessions SET ended_ms = ?, stop_reason = ?, summary = ? WHERE id = ?",
            (now_ms, summary.stop_reason, json.dumps(summary.__dict__), self.session),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


def _handle(
    raw: str | bytes,
    store: RecordStore,
    s: Summary,
    recv_ms: int,
    last_book: dict[str, int],
    lat: list[int],
) -> None:
    try:
        obj = json.loads(raw)
        d = obj["data"]
        kind = d["e"]
    except (ValueError, KeyError, TypeError):
        s.errors["parse"] = s.errors.get("parse", 0) + 1
        store.add("rec_events", (recv_ms, "parse_error", "unparseable message (body not stored)"))
        return
    s.counts[kind] = s.counts.get(kind, 0) + 1
    if kind == "depthUpdate":
        sym = str(d["s"])
        ev = int(d["E"])
        if sym in last_book:
            s.max_book_gap_ms[sym] = max(s.max_book_gap_ms.get(sym, 0), ev - last_book[sym])
        last_book[sym] = ev
        lat.append(recv_ms - ev)
        store.add(
            "rec_book",
            (
                sym,
                ev,
                int(d.get("T", ev)),
                recv_ms,
                int(d.get("u", 0)),
                json.dumps(d["b"]),
                json.dumps(d["a"]),
            ),
        )
    elif kind == "aggTrade":
        store.add(
            "rec_trades",
            (
                str(d["s"]),
                int(d["a"]),
                int(d["T"]),
                float(d["p"]),
                float(d["q"]),
                int(not d["m"]),
                recv_ms,
            ),
        )
    elif kind == "forceOrder":
        o = d["o"]
        store.add(
            "rec_liquidations",
            (
                str(o["s"]),
                int(o["T"]),
                str(o["S"]),
                float(o["p"]),
                float(o["ap"]),
                float(o["q"]),
                float(o["z"]),
                str(o["X"]),
                "observed",
                LIQUIDATION_COVERAGE,
            ),
        )
    else:
        store.add("rec_events", (recv_ms, "unknown_event", kind[:40]))


async def record(
    cfg: RecorderConfig,
    store: RecordStore,
    connect: Connector,
    *,
    clock: Callable[[], float] = time.monotonic,
    wall_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    sleep: Callable[[float], Any] = asyncio.sleep,
    rng: random.Random | None = None,
) -> Summary:
    rng = rng or random.Random()  # noqa: S311 — reconnect jitter, not security
    s = Summary()
    deadline = clock() + cfg.duration_s
    store.begin(cfg, wall_ms())
    last_book: dict[str, int] = {}
    lat: list[int] = []

    def remaining() -> float:
        return deadline - clock()

    async def run_one(category: str, url: str) -> None:
        attempt = 0
        while not s.stop_reason:
            if remaining() <= 0:
                s.stop_reason = "duration reached"
                return
            try:
                async with contextlib.AsyncExitStack() as stack:
                    ws = await asyncio.wait_for(
                        stack.enter_async_context(connect(url)),
                        timeout=max(0.01, min(cfg.open_timeout_s, remaining())),
                    )
                    store.add("rec_events", (wall_ms(), "connected", category))
                    while not s.stop_reason:
                        left = remaining()
                        if left <= 0:
                            s.stop_reason = "duration reached"
                            return
                        if cfg.stop_file is not None and cfg.stop_file.exists():
                            s.stop_reason = "stop file"
                            return
                        if s.bytes > cfg.max_bytes or (
                            s.messages % 1000 == 0 and store.size() > cfg.max_bytes
                        ):
                            s.stop_reason = "storage cap"
                            return
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=min(cfg.idle_timeout_s, left)
                        )
                        s.messages += 1
                        s.bytes += len(raw)
                        _handle(raw, store, s, wall_ms(), last_book, lat)
                        if store.pending() >= cfg.flush_every:
                            store.flush()
                        # a buffered socket can return without suspending;
                        # yield so the other connection is never starved
                        await asyncio.sleep(0)
                    return
            except TimeoutError:
                if remaining() <= 0:
                    s.stop_reason = "duration reached"
                    return
                store.add("rec_events", (wall_ms(), "idle_or_open_timeout", category))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — any socket error ends this connection; reconnect below
                name = type(exc).__name__
                s.errors[name] = s.errors.get(name, 0) + 1
                store.add("rec_events", (wall_ms(), "disconnect", f"{category}: {name}"))
            attempt += 1
            s.reconnects += 1
            if attempt > cfg.max_reconnects:
                s.stop_reason = f"too many reconnects ({category})"
                return
            backoff = min(30.0, 2.0**attempt) * rng.uniform(0.5, 1.5)
            await sleep(max(0.0, min(backoff, remaining())))

    tasks: list[asyncio.Task[None]] = []
    try:
        async with asyncio.timeout(cfg.duration_s + GRACE_S):
            tasks = [asyncio.create_task(run_one(c, u)) for c, u in cfg.urls().items()]
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # the first connection to finish ends the session for all of them
            s.stop_reason = s.stop_reason or "a connection ended"
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for t in done:
                exc = None if t.cancelled() else t.exception()
                if exc is not None:
                    raise exc
    except TimeoutError:
        s.stop_reason = s.stop_reason or "outer timeout"
    finally:
        for t in tasks:
            t.cancel()
        s.latency_ms_median = float(sorted(lat)[len(lat) // 2]) if lat else None
        s.stop_reason = s.stop_reason or "cancelled"
        store.end(wall_ms(), s)
    return s


def install_hard_stop(seconds: float, exit_code: int = 3) -> threading.Timer:
    """Last line of defence: end the PROCESS after *seconds*, even if the
    event loop is blocked. Returns the timer, so a clean run can cancel it."""
    timer = threading.Timer(seconds, lambda: os._exit(exit_code))
    timer.daemon = True
    timer.start()
    return timer


@contextlib.asynccontextmanager
async def websocket_connect(url: str) -> AsyncIterator[Conn]:
    """Public market-stream connection (no headers, no key)."""
    if not any(url.startswith(base) for base in ENDPOINTS.values()):
        raise ValueError("only Binance USD-M public market streams are allowed")
    from websockets.asyncio.client import connect  # lazy: optional at import time

    async with connect(url, open_timeout=10, close_timeout=5, max_size=2**20) as ws:
        yield ws


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stop-file")
    ap.add_argument(
        "--owner-approved",
        action="store_true",
        help="required to connect; the owner must have approved this session",
    )
    args = ap.parse_args(argv)
    cfg = RecorderConfig(
        symbols=tuple(args.symbols.split(",")),
        duration_s=args.minutes * 60,
        stop_file=Path(args.stop_file) if args.stop_file else None,
    )
    plan = {
        "urls": cfg.urls(),
        "duration_s": cfg.duration_s,
        "max_bytes": cfg.max_bytes,
        "max_reconnects": cfg.max_reconnects,
        "out": args.out,
    }
    print(json.dumps({"plan": plan}))
    if not args.owner_approved:
        print("not connecting: --owner-approved is required")
        return 2
    guard = install_hard_stop(cfg.duration_s + GRACE_S + 60)
    store = RecordStore(args.out)
    try:
        summary = asyncio.run(record(cfg, store, websocket_connect))
    finally:
        store.close()
        guard.cancel()
    print(json.dumps({"summary": summary.__dict__}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ENDPOINTS",
    "MAX_DURATION_S",
    "RecordStore",
    "RecorderConfig",
    "Summary",
    "install_hard_stop",
    "main",
    "record",
    "websocket_connect",
]
