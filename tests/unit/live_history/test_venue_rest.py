"""The venue client: GET-only allow-list, simulated hosts only, pacing, retries, no leaks."""

from __future__ import annotations

import ast
import hashlib
from collections import deque
from pathlib import Path

import httpx
import pytest

import jarvis.live_history as live_history
from jarvis.core.http_pool import HttpClientPool
from jarvis.live_history import venue_rest
from jarvis.live_history.simulated_venue import (
    BAD_SIGNATURE_CODE,
    BASE_URL,
    SIM_CREDENTIALS,
    SimulatedVenue,
)
from jarvis.live_history.venue_rest import (
    ALLOWED_PATHS,
    Credentials,
    ErrorKind,
    ForbiddenEndpoint,
    HostNotAllowed,
    VenueError,
    VenueReadClient,
    query_string,
    sign,
)
from tests.fakes.fake_live_venue import NOW_MS, FakeTime, make_client

PACKAGE = Path(live_history.__file__).parent

#: Write endpoints of the venue's documented API (and generic write words).
WRITE_WORDS = (
    "place_order",
    "batch_order",
    "modify_order",
    "cancel",
    "close_all",
    "flash_close",
    "change_leverage",
    "change_margin",
    "change_position",
    "adjust_position_margin",
    "transfer",
    "tp_sl_order",
    "withdraw",
)


def test_signature_follows_the_documented_recipe() -> None:
    creds = Credentials(api_key="yourApiKey", secret="yourSecretKey")  # noqa: S106 - test value
    body = '{"uid":"2899","arr":[{"id":1,"name":"maple"},{"id":2,"name":"lily"}]}'
    digest = hashlib.sha256(
        ("123456" + "20241120123045" + "yourApiKey" + "id1uid200" + body).encode()
    ).hexdigest()
    expected = hashlib.sha256((digest + "yourSecretKey").encode()).hexdigest()
    got = sign(creds, nonce="123456", timestamp="20241120123045", query="id1uid200", body=body)
    assert got == expected


def test_query_string_is_sorted_key_value_without_separators() -> None:
    assert query_string({"uid": "200", "id": "1"}) == "id1uid200"


def test_allow_list_is_read_only() -> None:
    assert len(ALLOWED_PATHS) == 6
    for path in ALLOWED_PATHS:
        assert path.startswith("/api/v1/futures/")
        assert not any(w in path for w in WRITE_WORDS), path
        tail = path.rsplit("/", 1)[-1]
        assert tail == "account" or tail.startswith("get_"), path


def test_package_has_no_write_path_and_no_body_sending_call() -> None:
    """A guard: no write endpoint name and no POST/PUT/PATCH/DELETE anywhere in the package."""
    for py in PACKAGE.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {"post", "put", "patch", "delete", "request", "stream", "send"}
            ):
                pytest.fail(f"{py.name}: .{node.func.attr}( — the venue client is GET-only")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                if low.startswith("/api/"):
                    assert node.value in ALLOWED_PATHS, f"{py.name}: {node.value}"
                for w in WRITE_WORDS:
                    if low.startswith("/") and w in low:
                        pytest.fail(f"{py.name}: write endpoint {node.value!r}")


def test_package_keeps_paper_code_and_models_out() -> None:
    """LIVE never imports the paper job's code (its fingerprint) nor any model SDK."""
    banned = ("jarvis.trading", "jarvis.market_data", "jarvis.brain", "anthropic", "openai")
    for py in PACKAGE.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(banned), f"{py.name} imports {name}"


async def test_forbidden_path_is_refused_before_any_request() -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    client = make_client(venue, FakeTime())
    with pytest.raises(ForbiddenEndpoint):
        await client.get("/api/v1/futures/trade/place_order", {"symbol": "BTCUSDT"})
    assert venue.log == []
    assert client.requests == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com",
        "https://10.0.0.1",
        "http://example.invalid",
        "https://localhost.example.com",
    ],
)
def test_only_simulated_hosts_are_accepted(url: str) -> None:
    with pytest.raises(HostNotAllowed):
        VenueReadClient(url, SIM_CREDENTIALS)


def test_faster_than_the_venue_limit_is_refused() -> None:
    with pytest.raises(ValueError):
        VenueReadClient(BASE_URL, SIM_CREDENTIALS, min_interval_s=0.05)


def test_credentials_never_print() -> None:
    creds = Credentials(api_key="k-123", secret="s-456")  # noqa: S106 - test value
    assert "k-123" not in repr(creds) and "s-456" not in repr(creds)
    assert "s-456" not in str(creds)


async def test_signed_read_returns_data_and_only_gets() -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    client = make_client(venue, FakeTime())
    data = await client.account("USDT")
    assert data[0]["marginCoin"] == "USDT"
    await client.pending_positions()
    await client.history_positions(start_ms=NOW_MS - 86_400_000, end_ms=NOW_MS, skip=0)
    assert [m for m, _, _ in venue.log] == ["GET", "GET", "GET"]
    assert client.requests == 3


async def test_requests_are_spaced() -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    time_ = FakeTime()
    client = make_client(venue, time_, min_interval_s=0.5)
    for _ in range(4):
        await client.pending_positions()
    assert time_.sleeps == [0.5, 0.5, 0.5]


async def test_at_the_venue_ceiling_the_simulator_never_rate_limits() -> None:
    time_ = FakeTime()
    venue = SimulatedVenue(now_ms=NOW_MS, clock=time_.clock)
    client = make_client(venue, time_, min_interval_s=0.1)
    for _ in range(30):
        await client.pending_positions()
    assert client.requests == 30


async def test_server_errors_are_retried_with_jitter_then_succeed() -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    venue.fail_next = deque([503, 502])
    time_ = FakeTime()
    client = make_client(venue, time_)
    assert await client.pending_positions() is not None
    assert client.requests == 3
    assert len(venue.log) == 3


async def test_persistent_failure_ends_as_typed_error() -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    venue.fail_next = deque([503] * 10)
    client = make_client(venue, FakeTime())
    with pytest.raises(VenueError) as err:
        await client.pending_positions()
    assert err.value.kind is ErrorKind.UNAVAILABLE
    assert client.requests == 1 + venue_rest.MAX_RETRIES


async def test_rate_limit_honours_retry_after() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"code": 0, "msg": "Success", "data": []})

    time_ = FakeTime()
    client = VenueReadClient(
        BASE_URL,
        SIM_CREDENTIALS,
        pool=HttpClientPool(transport=httpx.MockTransport(handler)),
        sleep=time_.sleep,
        clock=time_.clock,
    )
    assert await client.pending_positions() == []
    assert 2.0 in time_.sleeps


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_not_retried(status: int) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    venue.fail_next = deque([status])
    client = make_client(venue, FakeTime())
    with pytest.raises(VenueError) as err:
        await client.pending_positions()
    assert err.value.kind is ErrorKind.AUTH
    assert client.requests == 1


async def test_rejection_keeps_the_code_but_never_the_venue_text_or_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    wrong = Credentials(api_key="simulated-key", secret="not-the-secret")  # noqa: S106 - test value
    client = make_client(venue, FakeTime(), credentials=wrong)
    with pytest.raises(VenueError) as err:
        await client.pending_positions()
    assert err.value.kind is ErrorKind.REJECTED
    assert err.value.code == str(BAD_SIGNATURE_CODE)
    text = str(err.value) + caplog.text
    for leak in ("Signature error", "simulated-secret", "not-the-secret", "simulated-key"):
        assert leak not in text


async def test_unknown_envelope_is_a_bad_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    client = VenueReadClient(
        BASE_URL, SIM_CREDENTIALS, pool=HttpClientPool(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(VenueError) as err:
        await client.pending_positions()
    assert err.value.kind is ErrorKind.BAD_RESPONSE
    assert "maintenance" not in str(err.value)


async def test_network_failure_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    time_ = FakeTime()
    client = VenueReadClient(
        BASE_URL,
        SIM_CREDENTIALS,
        pool=HttpClientPool(transport=httpx.MockTransport(handler)),
        sleep=time_.sleep,
        clock=time_.clock,
    )
    with pytest.raises(VenueError) as err:
        await client.pending_positions()
    assert err.value.kind is ErrorKind.NETWORK
    assert len([s for s in time_.sleeps if s <= venue_rest.MAX_BACKOFF_S]) >= venue_rest.MAX_RETRIES


async def test_simulator_refuses_writes_and_unknown_paths() -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    async with httpx.AsyncClient(transport=venue.transport(), base_url=BASE_URL) as raw:
        assert (await raw.post("/api/v1/futures/trade/place_order", json={})).status_code == 405
        assert (await raw.get("/api/v1/futures/unknown")).status_code in (200, 404)


def test_page_limit_is_bounded() -> None:
    with pytest.raises(ValueError):
        venue_rest._window(0, 1, 0, 101)
