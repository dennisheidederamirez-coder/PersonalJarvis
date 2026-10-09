"""Local bar cache: one row per (source, interval, bar). Incremental updates
only fetch what is missing; sources are never mixed in one series."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jarvis.trading.data import BarSeries, make_series
from jarvis.trading.instruments import Instrument

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS md_bars (
        source TEXT NOT NULL,
        interval_ms INTEGER NOT NULL,
        ts INTEGER NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
        volume REAL NOT NULL,
        taker_buy REAL,
        PRIMARY KEY (source, interval_ms, ts)
    )""",
    """CREATE TABLE IF NOT EXISTS md_funding (
        source TEXT NOT NULL, ts INTEGER NOT NULL, rate REAL NOT NULL,
        PRIMARY KEY (source, ts)
    )""",
    """CREATE TABLE IF NOT EXISTS md_open_interest (
        source TEXT NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
        PRIMARY KEY (source, ts)
    )""",
)


class BarStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, series: BarSeries) -> int:
        buys = series.taker_buy
        rows = [
            (
                series.source,
                series.interval_ms,
                int(series.ts[k]),
                float(series.open[k]),
                float(series.high[k]),
                float(series.low[k]),
                float(series.close[k]),
                float(series.volume[k]),
                None if buys is None else float(buys[k]),
            )
            for k in range(len(series))
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO md_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (source, interval_ms, ts) DO UPDATE SET open = excluded.open,"
                " high = excluded.high, low = excluded.low, close = excluded.close,"
                " volume = excluded.volume, taker_buy = excluded.taker_buy",
                rows,
            )
        return len(rows)

    def load(
        self,
        source: str,
        instrument: Instrument,
        interval_ms: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> BarSeries:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, open, high, low, close, volume, taker_buy FROM md_bars"
                " WHERE source = ? AND interval_ms = ? AND ts >= ? AND ts < ? ORDER BY ts",
                (source, interval_ms, start_ms or 0, end_ms or 2**62),
            ).fetchall()
        buys = [r[6] for r in rows]
        return make_series(
            instrument,
            interval_ms,
            [tuple(r[:6]) for r in rows],
            source=source,
            retrieved_at=f"cache:{self.path.name}",
            taker_buy=buys if rows and all(b is not None for b in buys) else None,
        )

    def save_funding(self, source: str, points: Sequence[Any]) -> int:
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO md_funding VALUES (?, ?, ?)",
                [(source, int(p.ts_ms), float(p.rate)) for p in points],
            )
        return len(points)

    def save_open_interest(self, source: str, points: Sequence[Any]) -> int:
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO md_open_interest VALUES (?, ?, ?, ?)",
                [(source, int(p.ts_ms), float(p.value), str(p.unit)) for p in points],
            )
        return len(points)

    def load_funding(self, source: str) -> list[tuple[int, float]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, rate FROM md_funding WHERE source = ? ORDER BY ts", (source,)
            ).fetchall()
        return [(int(t), float(r)) for t, r in rows]

    def load_open_interest(self, source: str) -> list[tuple[int, float]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, value FROM md_open_interest WHERE source = ? ORDER BY ts", (source,)
            ).fetchall()
        return [(int(t), float(v)) for t, v in rows]

    def sources(self) -> list[tuple[str, int, int, int, int]]:
        """(source, interval_ms, bars, first ts, last ts) for everything cached."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT source, interval_ms, COUNT(*), MIN(ts), MAX(ts) FROM md_bars"
                " GROUP BY source, interval_ms ORDER BY source, interval_ms"
            ).fetchall()
        return [(str(a), int(b), int(c), int(d), int(e)) for a, b, c, d, e in rows]

    def last_ts(self, source: str, interval_ms: int) -> int | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(ts) FROM md_bars WHERE source = ? AND interval_ms = ?",
                (source, interval_ms),
            ).fetchone()
        return None if row is None or row[0] is None else int(row[0])


__all__ = ["BarStore"]
