"""The 60-minute recorder: every stop path, offline, plus the safety guards."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarvis.market_data import recorder as rec_mod
from jarvis.market_data.recorder import (
    MAX_DURATION_S,
    RecorderConfig,
    RecordStore,
    main,
    record,
    websocket_connect,
)
from tests.fakes.fake_market_stream import Connector, ScriptedConn, agg, depth, liquidation

T0 = 1_790_000_000_000


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def mixed(n: int) -> str:
    ts = T0 + n * 100
    return [
        depth("BTCUSDT", ts),
        agg("BTCUSDT", ts, n),
        liquidation("ETHUSDT", ts),
        depth("ETHUSDT", ts),
    ][n % 4]


async def no_sleep(_s: float) -> None:
    return None


async def test_an_endless_stream_stops_at_the_deadline(tmp_path: Path) -> None:
    clock = Clock()

    def tick() -> None:
        clock.t += 0.5

    conn = Connector([ScriptedConn(mixed, on_recv=tick)])
    store = RecordStore(tmp_path / "r.sqlite")
    s = await record(
        RecorderConfig(duration_s=60), store, conn, clock=clock, wall_ms=lambda: T0, sleep=no_sleep
    )
    store.close()
    assert s.stop_reason == "duration reached" and s.messages == 120 and clock.t <= 60.5
    db = sqlite3.connect(tmp_path / "r.sqlite")
    assert db.execute("SELECT COUNT(*) FROM rec_book").fetchone()[0] == 60
    assert db.execute("SELECT COUNT(*) FROM rec_trades").fetchone()[0] == 30
    assert db.execute("SELECT DISTINCT provenance, coverage FROM rec_liquidations").fetchall() == [
        ("observed", rec_mod.LIQUIDATION_COVERAGE)
    ]
    assert db.execute("SELECT stop_reason FROM rec_sessions").fetchone()[0] == "duration reached"
    assert "depth20@500ms" in conn.urls[0] and "@forceOrder" in conn.urls[0]


async def test_a_silent_socket_cannot_outlive_the_deadline(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "r.sqlite")
    start = time.monotonic()
    s = await record(
        RecorderConfig(duration_s=0.3, idle_timeout_s=30),
        store,
        Connector([ScriptedConn(mixed, hang_after=3)]),
        sleep=no_sleep,
    )
    assert s.stop_reason == "duration reached" and time.monotonic() - start < 2


async def test_an_open_that_never_completes_still_stops(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "r.sqlite")
    start = time.monotonic()
    s = await record(RecorderConfig(duration_s=0.3), store, Connector([None]), sleep=no_sleep)
    assert s.stop_reason == "duration reached" and time.monotonic() - start < 2


async def test_reconnects_are_jittered_and_limited(tmp_path: Path) -> None:
    slept: list[float] = []

    async def sleep(x: float) -> None:
        slept.append(x)

    conn = Connector([ConnectionError("down")])
    s = await record(
        RecorderConfig(duration_s=600, max_reconnects=3),
        RecordStore(tmp_path / "r.sqlite"),
        conn,
        sleep=sleep,
    )
    assert s.stop_reason == "too many reconnects" and len(conn.urls) == 4 and len(slept) == 3
    assert all(0.5 * 2**k <= x <= 1.5 * 2**k for k, x in enumerate(slept, start=1))


async def test_stop_file_and_storage_cap(tmp_path: Path) -> None:
    stop = tmp_path / "STOP"
    clock = Clock()

    def tick() -> None:
        clock.t += 1
        if clock.t >= 5:
            stop.touch()

    s = await record(
        RecorderConfig(duration_s=600, stop_file=stop),
        RecordStore(tmp_path / "a.sqlite"),
        Connector([ScriptedConn(mixed, tick)]),
        clock=clock,
        sleep=no_sleep,
    )
    assert s.stop_reason == "stop file" and s.messages == 5
    s2 = await record(
        RecorderConfig(duration_s=600, max_bytes=60_000),
        RecordStore(tmp_path / "b.sqlite"),
        Connector([ScriptedConn(mixed)]),
        clock=Clock(),
        sleep=no_sleep,
    )
    assert s2.stop_reason == "storage cap" and s2.bytes > 60_000 and s2.messages > 0


async def test_bad_messages_are_counted_not_stored(tmp_path: Path) -> None:
    clock = Clock()

    def tick() -> None:
        clock.t += 1

    msgs = ["not json {secret-ish body}", '{"stream": "x", "data": {"e": "markPriceUpdate"}}']
    s = await record(
        RecorderConfig(duration_s=2),
        RecordStore(tmp_path / "r.sqlite"),
        Connector([ScriptedConn(lambda n: msgs[(n - 1) % 2], tick)]),
        clock=clock,
        sleep=no_sleep,
    )
    db = sqlite3.connect(tmp_path / "r.sqlite")
    details = [r[0] for r in db.execute("SELECT detail FROM rec_events")]
    assert s.errors == {"parse": 1} and not any("secret" in d for d in details)


def test_limits_are_enforced_by_the_config() -> None:
    with pytest.raises(ValueError):
        RecorderConfig(duration_s=MAX_DURATION_S + 1)
    with pytest.raises(ValueError):
        RecorderConfig(symbols=("btcusdt; drop",))
    assert RecorderConfig().url().startswith("wss://fstream.binance.com/stream?streams=")


async def test_only_public_market_streams_can_be_opened() -> None:
    with pytest.raises(ValueError):
        async with websocket_connect("wss://fstream.binance.com/ws/listenKey"):
            pass


def test_no_keys_and_no_order_endpoints_in_the_recorder() -> None:
    src = Path(rec_mod.__file__).read_text(encoding="utf-8")
    for banned in ("X-MBX-APIKEY", "apiKey", "signature", "listenKey", "/order", "get_secret"):
        assert banned not in src.replace("ws/listenKey", "") or banned == "listenKey"
    assert "get_secret" not in src and "X-MBX-APIKEY" not in src and "/fapi/v1/order" not in src


def test_the_cli_refuses_to_connect_without_owner_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--out", str(tmp_path / "x.sqlite"), "--minutes", "60"]) == 2
    assert "not connecting" in capsys.readouterr().out
    assert not (tmp_path / "x.sqlite").exists()
    with pytest.raises(ValueError):
        main(["--out", str(tmp_path / "x.sqlite"), "--minutes", "61"])


def test_the_watchdog_ends_a_wedged_process() -> None:
    code = (
        "import time; from jarvis.market_data.recorder import install_hard_stop; "
        "install_hard_stop(0.5); time.sleep(30); print('still alive')"
    )
    start = time.monotonic()
    p = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=Path(__file__).resolve().parents[3],
    )
    assert p.returncode == 3 and "still alive" not in p.stdout and time.monotonic() - start < 10
