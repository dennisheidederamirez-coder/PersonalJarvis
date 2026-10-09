"""The paper runner offline: pre-registration lock, closed bars only, exactly
once across restarts and crashes, stale or disputed data, execution faults,
liquidity limits, and the status report."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from jarvis.trading.data import BarSeries
from jarvis.trading.journal import SqliteJournal
from jarvis.trading.paper import CostModel
from jarvis.trading.paper_runner import PaperRunner, StreamData
from jarvis.trading.paper_spec import DEFAULT_SPEC, INSTRUMENTS, SpecError, StreamSpec
from jarvis.trading.timeframes import D1, H4, resample
from tests.fakes.fake_market import T0, trending

SEEDS = {"BTC-USD": 2, "ETH-USD": 3, "SOL-USD": 4, "XRP-USD": 5, "BNB-USD": 6}
HOURS = 24 * 420


def _market() -> dict[str, BarSeries]:
    out: dict[str, BarSeries] = {}
    for stream in DEFAULT_SPEC.streams:
        hourly = trending(HOURS, seed=SEEDS[stream.symbol], instrument=INSTRUMENTS[stream.symbol])
        s = resample(hourly, stream.interval_ms)
        out[stream.id] = dataclasses.replace(s, source=stream.primary_source)
    return out


MARKET = _market()


class Feed:
    """Serves every bar that has STARTED by ``now`` — including the one still
    forming, which the runner must ignore."""

    def __init__(self, market: dict[str, BarSeries]) -> None:
        self.market = market
        self.frozen_at: int | None = None  # simulate a dead feed
        self.disputed: set[str] = set()

    def __call__(self, stream: StreamSpec, now: int) -> StreamData:
        if stream.id in self.disputed:
            return StreamData(None, False, "sources disagree on 3.0% of bars")
        s = self.market[stream.id]
        upto = now if self.frozen_at is None else min(now, self.frozen_at)
        k = int((s.ts <= upto).sum())
        return StreamData(s.slice(0, k), True, "ok", None)


def _runner(tmp: Path, feed: Feed, now: list[int], **kw: Any) -> PaperRunner:
    return PaperRunner(
        DEFAULT_SPEC, SqliteJournal(tmp / "paper.sqlite"), feed, now_ms=lambda: now[0], **kw
    )


START = T0 + 200 * D1 + 5 * 60_000  # five minutes after a daily close


def _run_days(runner: PaperRunner, now: list[int], days: int, *, every: int = H4) -> None:
    for _ in range(days * (D1 // every)):
        now[0] += every
        runner.step()


def _trades(tmp: Path) -> list[dict[str, Any]]:
    return [d for _ts, _k, _s, d in SqliteJournal(tmp / "paper.sqlite").read("trade")]


# --- the pre-registration lock ---------------------------------------------------------


def test_the_spec_is_frozen_and_checked() -> None:
    assert DEFAULT_SPEC.digest() == DEFAULT_SPEC.digest()
    assert [s.id for s in DEFAULT_SPEC.streams] == [
        "A:BTC-USD",
        "A:ETH-USD",
        "A:SOL-USD",
        "A:XRP-USD",
        "A:BNB-USD",
        "B:BTC-USD",
    ]
    assert DEFAULT_SPEC.leverage == 1.0 and DEFAULT_SPEC.risk_per_trade == 0.005
    with pytest.raises(SpecError):
        dataclasses.replace(DEFAULT_SPEC, leverage=2.0).validate()
    with pytest.raises(SpecError):
        dataclasses.replace(DEFAULT_SPEC, risk_per_trade=0.01).validate()
    tweaked = dataclasses.replace(
        DEFAULT_SPEC,
        streams=(
            dataclasses.replace(
                DEFAULT_SPEC.streams[0], params={**DEFAULT_SPEC.streams[0].params, "entry_n": 21}
            ),
            *DEFAULT_SPEC.streams[1:],
        ),
    )
    assert tweaked.digest() != DEFAULT_SPEC.digest()


def test_a_changed_spec_cannot_run_on_an_existing_test(tmp_path: Path) -> None:
    now = [START]
    with pytest.raises(SpecError):
        _runner(tmp_path, Feed(MARKET), now, expected_digest="0" * 64)
    _runner(tmp_path, Feed(MARKET), now).step()
    tweaked = dataclasses.replace(DEFAULT_SPEC, max_volume_share=0.02)
    with pytest.raises(SpecError):
        PaperRunner(
            tweaked, SqliteJournal(tmp_path / "paper.sqlite"), Feed(MARKET), now_ms=lambda: now[0]
        )


# --- closed bars, exactly once ---------------------------------------------------------------


def test_the_first_step_only_warms_up_and_ignores_the_forming_bar(tmp_path: Path) -> None:
    now = [START + 3 * 3_600_000]  # mid-way through a 4h bar and a day
    runner = _runner(tmp_path, Feed(MARKET), now)
    report = runner.step()
    assert set(report.processed) == {s.id for s in DEFAULT_SPEC.streams}
    assert all(n == 1 for n in report.processed.values())
    last = runner.last_bar
    assert last["A:BTC-USD"] + D1 <= now[0] and last["B:BTC-USD"] + H4 <= now[0]
    assert last["B:BTC-USD"] + 2 * H4 > now[0]  # the newest CLOSED 4h bar, not the forming one


def test_catching_up_once_equals_stepping_every_four_hours(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    now_a, now_b = [START], [START]
    ra, rb = _runner(a, Feed(MARKET), now_a), _runner(b, Feed(MARKET), now_b)
    ra.step()
    rb.step()
    _run_days(ra, now_a, 150)
    now_b[0] = now_a[0]
    rb.step()  # one big catch-up after a long pause
    ta, tb = _trades(a), _trades(b)
    assert len(ta) > 10 and [t["client_id"] for t in ta] == [t["client_id"] for t in tb]
    assert [round(t["net"], 6) for t in ta] == [round(t["net"], 6) for t in tb]
    ids = [t["client_id"] for t in ta]
    assert len(ids) == len(set(ids))  # no trade twice


def test_restarts_and_a_crash_before_commit_change_nothing(tmp_path: Path) -> None:
    ref, crashy = tmp_path / "ref", tmp_path / "crash"
    ref.mkdir()
    crashy.mkdir()
    now_r = [START]
    rr = _runner(ref, Feed(MARKET), now_r)
    rr.step()
    _run_days(rr, now_r, 60, every=D1)

    now_c = [START]
    _runner(crashy, Feed(MARKET), now_c).step()
    crashes = {7, 23}
    for day in range(60):
        now_c[0] += D1
        runner = _runner(crashy, Feed(MARKET), now_c)  # a fresh process every day
        if day in crashes:
            boom = _runner(crashy, Feed(MARKET), now_c, _fault_before_commit=lambda n: n == 2)
            with pytest.raises(RuntimeError):
                boom.step()
            runner = _runner(crashy, Feed(MARKET), now_c)  # restart after the crash
        runner.step()
    assert [t["client_id"] for t in _trades(ref)] == [t["client_id"] for t in _trades(crashy)]
    assert rr.status()["candidates"] == _runner(crashy, Feed(MARKET), now_c).status()["candidates"]


# --- data problems ----------------------------------------------------------------------------


def test_stale_data_pauses_and_the_gap_is_processed_once_later(tmp_path: Path) -> None:
    feed = Feed(MARKET)
    now = [START]
    runner = _runner(tmp_path, feed, now)
    runner.step()
    feed.frozen_at = now[0]
    now[0] += 3 * D1
    report = runner.step()
    assert report.skipped["A:BTC-USD"] == "data stale" and report.processed == {}
    assert runner.status()["data"]["A:BTC-USD"] == "data stale"
    feed.frozen_at = None
    report = runner.step()
    assert report.processed["A:BTC-USD"] == 3  # the missed days, once each
    assert report.processed["B:BTC-USD"] == 18


def test_disputed_sources_mean_no_decision(tmp_path: Path) -> None:
    feed = Feed(MARKET)
    feed.disputed = {"A:SOL-USD"}
    now = [START]
    runner = _runner(tmp_path, feed, now)
    report = runner.step()
    assert "A:SOL-USD" not in report.processed
    assert "disagree" in report.skipped["A:SOL-USD"]


# --- execution realism ---------------------------------------------------------------------


def test_execution_faults_are_journaled_and_open_nothing(tmp_path: Path) -> None:
    now = [START]
    runner = _runner(tmp_path, Feed(MARKET), now, execution_fault=lambda cid: "venue timeout")
    runner.step()
    _run_days(runner, now, 60, every=D1)
    journal = SqliteJournal(tmp_path / "paper.sqlite")
    failed = journal.read("execution_failed")
    assert failed and all(d["reason"] == "venue timeout" for *_x, d in failed)
    assert journal.read("fill") == [] and runner.status()["candidates"]["A"]["open_positions"] == []


def test_thin_markets_hit_the_liquidity_cap(tmp_path: Path) -> None:
    thin = {k: dataclasses.replace(v, volume=v.volume * 1e-4) for k, v in MARKET.items()}
    now = [START]
    runner = _runner(tmp_path, Feed(thin), now)
    runner.step()
    _run_days(runner, now, 60, every=D1)
    cancelled = SqliteJournal(tmp_path / "paper.sqlite").read("cancelled")
    assert any("insufficient liquidity" in d["reason"] for *_x, d in cancelled)
    assert _trades(tmp_path) == []


def test_spread_is_paid_on_every_fill() -> None:
    with_spread = CostModel(slippage_bps=5.0, spread_bps=4.0)
    assert with_spread.fill_price(100.0, buy=True) == pytest.approx(100.07)
    assert with_spread.fill_price(100.0, buy=False) == pytest.approx(99.93)


# --- status ----------------------------------------------------------------------------------


def test_the_status_report(tmp_path: Path) -> None:
    now = [START]
    runner = _runner(tmp_path, Feed(MARKET), now)
    runner.step()
    _run_days(runner, now, 120, every=D1)
    status = runner.status()
    a, b = status["candidates"]["A"], status["candidates"]["B"]
    for c in (a, b):
        assert {
            "equity",
            "cash",
            "return_pct",
            "drawdown_now",
            "max_drawdown",
            "kill_switch",
            "open_positions",
            "trades",
            "evaluable",
            "by_strategy",
            "last_bar",
        } <= set(c)
    assert a["trades"] > 0 and not a["evaluable"]  # fewer than 40 trades: not judged yet
    assert set(a["by_strategy"]) <= {s.id for s in DEFAULT_SPEC.streams if s.candidate == "A"}
    text = runner.status_text()
    assert "simulation only" in text and "[A]" in text and "[B]" in text
