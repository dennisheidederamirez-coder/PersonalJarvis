"""Helpers for the live-history tests: a frozen clock and a client wired to the simulated venue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from jarvis.core.http_pool import HttpClientPool
from jarvis.live_history.simulated_venue import BASE_URL, SIM_CREDENTIALS, SimulatedVenue
from jarvis.live_history.venue_rest import Credentials, VenueReadClient

NOW_MS = 1_790_000_000_000  # a fixed "now" (2026-09-21 UTC)


@dataclass
class FakeTime:
    """A monotonic clock that only moves when the code under test sleeps."""

    t: float = 1_000.0
    sleeps: list[float] = field(default_factory=list)

    def clock(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_client(
    venue: SimulatedVenue,
    time_: FakeTime,
    *,
    credentials: Credentials = SIM_CREDENTIALS,
    min_interval_s: float = 0.5,
    base_url: str = BASE_URL,
    wall_ms: Callable[[], int] = lambda: NOW_MS,
) -> VenueReadClient:
    return VenueReadClient(
        base_url,
        credentials,
        pool=HttpClientPool(timeout_s=5.0, transport=venue.transport()),
        min_interval_s=min_interval_s,
        sleep=time_.sleep,
        clock=time_.clock,
        wall_ms=wall_ms,
    )
