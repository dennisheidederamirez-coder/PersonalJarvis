"""Polite JSON GETs: pooled client, minimum spacing, bounded jittered retries."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from jarvis.core.http_pool import HttpClientPool

log = logging.getLogger(__name__)

MAX_RETRIES: Final = 3
BASE_BACKOFF_S: Final = 1.0
MAX_BACKOFF_S: Final = 30.0
RETRYABLE: Final = frozenset({429, 500, 502, 503, 504})


class MarketDataError(RuntimeError):
    """A venue request failed; carries only the venue, status and code."""

    def __init__(self, venue: str, status: int | None, code: str = "") -> None:
        self.venue, self.status, self.code = venue, status, code
        super().__init__(f"{venue}: HTTP {status}" + (f" code {code}" if code else ""))


class PoliteClient:
    def __init__(
        self,
        venue: str,
        *,
        min_interval_s: float,
        pool: HttpClientPool | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.venue = venue
        self.min_interval_s = min_interval_s
        self._pool = pool or HttpClientPool(timeout_s=15.0)
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()  # noqa: S311 - retry jitter, not security
        self._last = float("-inf")
        self._lock = asyncio.Lock()
        self.requests = 0

    async def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(MAX_RETRIES + 1):
            async with self._lock:
                wait = self._last + self.min_interval_s - self._clock()
                if wait > 0:
                    await self._sleep(wait)
                self._last = self._clock()
                self.requests += 1
            try:
                resp = await self._pool.client().get(url, params=clean)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — transport errors are retried, then raised
                if attempt == MAX_RETRIES:
                    raise MarketDataError(self.venue, None, type(exc).__name__) from None
                await self._sleep(self._backoff(attempt, None))
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in RETRYABLE and attempt < MAX_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                log.warning("market data: %s answered %s, retrying", self.venue, resp.status_code)
                await self._sleep(self._backoff(attempt, retry_after))
                continue
            raise MarketDataError(self.venue, resp.status_code)
        raise MarketDataError(self.venue, None, "retries exhausted")  # pragma: no cover

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(MAX_BACKOFF_S, max(0.0, float(retry_after)))
            except ValueError:
                pass  # an HTTP-date or garbage: fall back to the jittered backoff
        cap = min(MAX_BACKOFF_S, BASE_BACKOFF_S * 2**attempt)
        return self._rng.uniform(0, cap)  # full jitter


__all__ = ["MarketDataError", "PoliteClient"]
