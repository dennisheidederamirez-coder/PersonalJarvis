"""The primary venue's signed futures REST API — READ-ONLY, allow-listed, rate-limited.

Safety boundaries, each enforced in code and pinned by a test:

1. **GET only, allow-listed paths.** ``ALLOWED_PATHS`` names the six read
   endpoints this package needs (account, open positions, closed positions,
   fills, order history, open orders). Any other path raises
   ``ForbiddenEndpoint`` before a request is built; the client has no method
   that sends a body. There is no order, cancel, leverage, margin-mode,
   position-mode or transfer path anywhere in the package.
2. **Simulated hosts only (phase 2).** ``SIMULATED_HOSTS`` is the complete
   list of hosts the client accepts; the real venue host is NOT on it. Live
   access is a deliberate code change for a later, owner-approved phase.
3. **Bounded request rate.** Requests are spaced at least ``min_interval_s``
   apart (default 0.5 s = 2/s; the venue allows 10/s per account, and
   ``MIN_INTERVAL_S`` refuses anything faster than that). Transport errors,
   429 and 5xx are retried at most ``MAX_RETRIES`` times with full-jitter
   backoff (AP-33); ``Retry-After`` is honoured up to ``MAX_BACKOFF_S``.
4. **Nothing secret or provider-written leaves the client.** Errors carry the
   kind, the HTTP status and the venue's numeric code — never the venue's
   message text, a header, the key or a signature (AP-34). ``Credentials``
   masks itself in ``repr``.

Signing (venue docs, "Sign"): ``digest = sha256(nonce + timestamp + api_key +
query + body)``, ``sign = sha256(digest + secret)``, where ``query`` is the
parameters sorted by key and concatenated as ``key + value`` with no
separators, and ``body`` is empty for a GET.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

from jarvis.core.http_pool import HttpClientPool

log = logging.getLogger(__name__)

ACCOUNT: Final = "/api/v1/futures/account"
PENDING_POSITIONS: Final = "/api/v1/futures/position/get_pending_positions"
HISTORY_POSITIONS: Final = "/api/v1/futures/position/get_history_positions"
HISTORY_TRADES: Final = "/api/v1/futures/trade/get_history_trades"
HISTORY_ORDERS: Final = "/api/v1/futures/trade/get_history_orders"
PENDING_ORDERS: Final = "/api/v1/futures/trade/get_pending_orders"

ALLOWED_PATHS: Final = frozenset(
    {ACCOUNT, PENDING_POSITIONS, HISTORY_POSITIONS, HISTORY_TRADES, HISTORY_ORDERS, PENDING_ORDERS}
)

#: The only hosts this client talks to in phase 2. ``.invalid`` is reserved
#: (RFC 2606) and never resolves, so a misrouted request cannot leave the box.
SIMULATED_HOSTS: Final = frozenset({"simulated-venue.invalid", "127.0.0.1", "localhost"})

#: Page size the venue allows at most.
PAGE_LIMIT: Final = 100
#: The venue's documented ceiling is 10 requests per second per account.
MIN_INTERVAL_S: Final = 0.1
DEFAULT_INTERVAL_S: Final = 0.5
MAX_RETRIES: Final = 3
BASE_BACKOFF_S: Final = 1.0
MAX_BACKOFF_S: Final = 30.0
RETRYABLE: Final = frozenset({429, 500, 502, 503, 504})


class ForbiddenEndpoint(PermissionError):
    """A path outside ``ALLOWED_PATHS`` — a programming error, never retried."""


class HostNotAllowed(PermissionError):
    """A base URL outside ``SIMULATED_HOSTS``."""


class ErrorKind(StrEnum):
    NETWORK = "network"  # no answer (after retries)
    RATE_LIMITED = "rate_limited"  # 429 after retries
    UNAVAILABLE = "unavailable"  # 5xx after retries
    AUTH = "auth"  # 401/403: key, signature, clock or IP binding
    REJECTED = "rejected"  # HTTP 200 with a non-zero venue code
    BAD_RESPONSE = "bad_response"  # not the documented envelope


class VenueError(RuntimeError):
    """A failed read; carries no provider text (AP-34)."""

    def __init__(self, kind: ErrorKind, *, status: int | None = None, code: str = "") -> None:
        self.kind, self.status, self.code = kind, status, code
        parts = (
            [kind.value]
            + ([f"HTTP {status}"] if status else [])
            + ([f"code {code}"] if code else [])
        )
        super().__init__("venue read failed: " + ", ".join(parts))


@dataclass(frozen=True)
class Credentials:
    api_key: str = field(repr=False)
    secret: str = field(repr=False)

    def __repr__(self) -> str:
        return "Credentials(api_key=***, secret=***)"


def query_string(params: Mapping[str, Any]) -> str:
    """The signing form of the query: sorted by key, ``key + value``, no separators."""
    return "".join(f"{k}{params[k]}" for k in sorted(params))


def sign(creds: Credentials, *, nonce: str, timestamp: str, query: str, body: str = "") -> str:
    digest = hashlib.sha256((nonce + timestamp + creds.api_key + query + body).encode()).hexdigest()
    return hashlib.sha256((digest + creds.secret).encode()).hexdigest()


class VenueReadClient:
    def __init__(
        self,
        base_url: str,
        credentials: Credentials,
        *,
        pool: HttpClientPool | None = None,
        min_interval_s: float = DEFAULT_INTERVAL_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        nonce: Callable[[], str] = lambda: secrets.token_hex(16),
        rng: random.Random | None = None,
    ) -> None:
        host = urlsplit(base_url).hostname or ""
        if host not in SIMULATED_HOSTS:
            raise HostNotAllowed(
                f"host {host!r} is not a simulated venue; live access is not enabled"
            )
        if min_interval_s < MIN_INTERVAL_S:
            raise ValueError(f"min_interval_s below the venue limit ({MIN_INTERVAL_S}s)")
        self._base = base_url.rstrip("/")
        self._creds = credentials
        self._pool = pool or HttpClientPool(timeout_s=15.0)
        self._interval = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._wall_ms = wall_ms
        self._nonce = nonce
        self._rng = rng or random.Random()  # noqa: S311 - retry jitter, not security
        self._last = float("-inf")
        self._lock = asyncio.Lock()
        self.requests = 0

    # ---------------------------------------------------------------- endpoints

    async def account(self, margin_coin: str) -> Any:
        return await self.get(ACCOUNT, {"marginCoin": margin_coin})

    async def pending_positions(self) -> Any:
        return await self.get(PENDING_POSITIONS, {})

    async def pending_orders(self, *, skip: int, limit: int = PAGE_LIMIT) -> Any:
        return await self.get(PENDING_ORDERS, {"skip": skip, "limit": limit})

    async def history_positions(
        self, *, start_ms: int, end_ms: int, skip: int, limit: int = PAGE_LIMIT
    ) -> Any:
        return await self.get(HISTORY_POSITIONS, _window(start_ms, end_ms, skip, limit))

    async def history_position(self, position_id: str) -> Any:
        """One closed position by id (a position seen open that has since vanished)."""
        return await self.get(HISTORY_POSITIONS, {"positionId": position_id, "limit": 1})

    async def history_order(self, order_id: str) -> Any:
        """One finished order by id (an order seen open that has since vanished)."""
        return await self.get(HISTORY_ORDERS, {"orderId": order_id, "limit": 1})

    async def history_trades(
        self, *, start_ms: int, end_ms: int, skip: int, limit: int = PAGE_LIMIT
    ) -> Any:
        return await self.get(HISTORY_TRADES, _window(start_ms, end_ms, skip, limit))

    async def history_orders(
        self, *, start_ms: int, end_ms: int, skip: int, limit: int = PAGE_LIMIT
    ) -> Any:
        return await self.get(HISTORY_ORDERS, _window(start_ms, end_ms, skip, limit))

    # ------------------------------------------------------------------- engine

    async def get(self, path: str, params: Mapping[str, Any]) -> Any:
        """One signed GET; returns the envelope's ``data``."""
        if path not in ALLOWED_PATHS:
            raise ForbiddenEndpoint(f"{path!r} is not an allowed read endpoint")
        clean = {
            k: str(v).lower() if isinstance(v, bool) else str(v)
            for k, v in params.items()
            if v is not None
        }
        for attempt in range(MAX_RETRIES + 1):
            await self._space()
            headers = self._headers(clean)
            try:
                resp = await self._pool.client().get(
                    self._base + path, params=clean, headers=headers
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — transport errors are retried, then raised as NETWORK
                if attempt == MAX_RETRIES:
                    log.warning("live history: venue unreachable (%s)", type(exc).__name__)
                    raise VenueError(ErrorKind.NETWORK) from None
                await self._sleep(self._backoff(attempt, None))
                continue
            status = resp.status_code
            if status in RETRYABLE:
                if attempt < MAX_RETRIES:
                    log.info("live history: venue answered %s, retrying", status)
                    await self._sleep(self._backoff(attempt, resp.headers.get("Retry-After")))
                    continue
                kind = ErrorKind.RATE_LIMITED if status == 429 else ErrorKind.UNAVAILABLE
                raise VenueError(kind, status=status)
            if status in (401, 403):
                raise VenueError(ErrorKind.AUTH, status=status)
            if status != 200:
                raise VenueError(ErrorKind.BAD_RESPONSE, status=status)
            return _unwrap(resp)
        raise VenueError(ErrorKind.NETWORK)  # pragma: no cover - the loop always returns or raises

    async def _space(self) -> None:
        async with self._lock:
            wait = self._last + self._interval - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._last = self._clock()
            self.requests += 1

    def _headers(self, params: Mapping[str, str]) -> dict[str, str]:
        nonce = self._nonce()
        timestamp = str(self._wall_ms())
        return {
            "api-key": self._creds.api_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": sign(self._creds, nonce=nonce, timestamp=timestamp, query=query_string(params)),
            "Content-Type": "application/json",
        }

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(MAX_BACKOFF_S, max(0.0, float(retry_after)))
            except ValueError:
                pass  # an HTTP-date or garbage: fall back to the jittered backoff
        return self._rng.uniform(0, min(MAX_BACKOFF_S, BASE_BACKOFF_S * 2**attempt))


def _window(start_ms: int, end_ms: int, skip: int, limit: int) -> dict[str, Any]:
    if not 1 <= limit <= PAGE_LIMIT:
        raise ValueError(f"limit must be 1..{PAGE_LIMIT}")
    return {"startTime": start_ms, "endTime": end_ms, "skip": skip, "limit": limit}


def _unwrap(resp: Any) -> Any:
    try:
        body = resp.json()
    except ValueError:  # reported as BAD_RESPONSE; the body is not logged (AP-34)
        raise VenueError(ErrorKind.BAD_RESPONSE, status=200) from None
    if not isinstance(body, dict) or "code" not in body:
        raise VenueError(ErrorKind.BAD_RESPONSE, status=200)
    code = body["code"]
    if code != 0:
        # The venue's ``msg`` is provider-written text: it stays out (AP-34).
        raise VenueError(ErrorKind.REJECTED, status=200, code=str(code))
    return body.get("data")


__all__ = [
    "ACCOUNT",
    "ALLOWED_PATHS",
    "HISTORY_ORDERS",
    "HISTORY_POSITIONS",
    "HISTORY_TRADES",
    "MIN_INTERVAL_S",
    "PAGE_LIMIT",
    "PENDING_ORDERS",
    "PENDING_POSITIONS",
    "SIMULATED_HOSTS",
    "Credentials",
    "ErrorKind",
    "ForbiddenEndpoint",
    "HostNotAllowed",
    "VenueError",
    "VenueReadClient",
    "query_string",
    "sign",
]
