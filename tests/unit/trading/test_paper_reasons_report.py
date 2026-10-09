"""Every bar the paper runner does not trade on gets a reason; the daily
report adds up money, costs, risk, data and those reasons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from jarvis.trading.journal import SqliteJournal
from jarvis.trading.paper_report import daily_report, to_markdown
from jarvis.trading.paper_runner import PaperRunner
from jarvis.trading.paper_spec import DEFAULT_SPEC
from jarvis.trading.strategies import DonchianBreakout, SmaCross
from jarvis.trading.timeframes import D1, H4
from tests.fakes.fake_market import random_walk

from .test_paper_runner import MARKET, START, Feed, _run_days


def _runner(tmp: Path, feed: Feed, now: list[int]) -> PaperRunner:
    return PaperRunner(DEFAULT_SPEC, SqliteJournal(tmp / "p.sqlite"), feed, now_ms=lambda: now[0])


def test_strategies_explain_why_they_did_not_act() -> None:
    s = random_walk(400, seed=3)
    d, m = DonchianBreakout(20, 20), SmaCross(20, 100)
    d.prepare(s)
    m.prepare(s)
    assert d.explain(5, None).startswith("warming up")
    assert m.explain(50, None).startswith("warming up")
    quiet = next(i for i in range(30, 400) if d.decide(i, None) is None)
    assert d.explain(quiet, None).startswith("no breakout: close")
    calm = next(i for i in range(120, 400) if m.decide(i, None) is None)
    assert m.explain(calm, None).startswith("no SMA crossover")


def test_every_untraded_bar_and_every_data_problem_has_a_reason(tmp_path: Path) -> None:
    feed = Feed(MARKET)
    now = [START]
    runner = _runner(tmp_path, feed, now)
    runner.step()
    _run_days(runner, now, 20)
    j = SqliteJournal(tmp_path / "p.sqlite")
    bars = {(ts, d["stream"]) for ts, k, _s, d in j.read() if k == "bar"}
    explained = {
        (ts, d["strategy"])
        for ts, k, _s, d in j.read()
        if k in ("no_signal", "hold", "decision") and "strategy" in d
    }
    assert bars and bars <= explained  # every processed bar: a decision or a reason
    reasons = [d["reason"] for *_x, d in j.read("no_signal")]
    assert any(r.startswith(("no breakout", "no SMA crossover", "warming up")) for r in reasons)
    waiting = [d["reason"] for *_x, d in j.read("waiting")]
    assert any("no new closed bar since" in r for r in waiting)  # 4h steps, daily bars
    feed.disputed = {"A:XRP-USD"}
    now[0] += H4
    runner.step()
    data = [d for *_x, d in j.read("no_signal") if d.get("stream") == "A:XRP-USD"]
    assert data and data[-1]["reason"].startswith("data: sources disagree")


def test_the_daily_report(tmp_path: Path) -> None:
    now = [START]
    runner = _runner(tmp_path, Feed(MARKET), now)
    runner.step()
    _run_days(runner, now, 150, every=D1)
    day = (datetime.fromtimestamp(now[0] / 1000, UTC) - timedelta(days=1)).date()
    report = daily_report(runner, day)
    a = report["candidates"]["A"]
    assert {
        "equity",
        "cash",
        "realized_today",
        "realized_total",
        "unrealized",
        "drawdown_now",
        "max_drawdown",
        "fees_today",
        "fees_total",
        "funding_total",
        "trades_today",
        "trades_total",
        "open_positions",
        "not_traded_because",
    } <= set(a)
    assert a["trades_total"] > 0 and a["fees_total"] > 0
    j = SqliteJournal(tmp_path / "p.sqlite")
    assert j.read("fill") and j.read("no_signal")  # trades, and reasons in between
    expected = DEFAULT_SPEC.capital_per_candidate + a["realized_total"] + a["unrealized"]
    assert abs(a["equity"] - expected) < 1.0  # funding of open positions is the only gap
    assert set(report["data"]) == {s.id for s in DEFAULT_SPEC.streams}
    md = to_markdown(report)
    assert "simulation only" in md and "## Candidate A" in md and "## Data" in md
