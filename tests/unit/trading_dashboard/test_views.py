"""The dashboard's sections against journals written by the real paper runner."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jarvis.market_data import paper_job
from jarvis.trading.paper_spec import DEFAULT_SPEC
from jarvis.trading.timeframes import D1, H4
from jarvis.trading_dashboard import views as v
from jarvis.trading_dashboard.reader import JournalReader, JournalSnapshot, ReadFailure, SourceState
from jarvis.trading_dashboard.reasons import CODES, classify
from tests.fakes.fake_paper_journal import PaperRun, enable, run_paper_test


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> PaperRun:
    return run_paper_test(tmp_path_factory.mktemp("paper"), days=3)


def _ctx(run: PaperRun, now: int | None = None) -> v.DashboardContext:
    snap = JournalReader(run.path).read()
    assert isinstance(snap, JournalSnapshot)
    return v.context(snap, DEFAULT_SPEC, now if now is not None else run.now[0] + 60_000)


# --- the job's schedule, copied, must stay the job's ------------------------------


def test_schedule_and_budgets_match_the_paper_job() -> None:
    assert v.SLOT_HOURS == paper_job.SLOT_HOURS
    assert v.SLOT_DELAY_MIN == paper_job.SLOT_DELAY_MIN
    assert v.MAX_REQUESTS_PER_RUN == paper_job.MAX_REQUESTS_PER_RUN
    assert v.MAX_REQUESTS_PER_DAY == paper_job.MAX_REQUESTS_PER_DAY
    for minute in range(0, 3 * 24 * 60, 37):
        after = datetime(2026, 10, 9, tzinfo=UTC).timestamp() * 1000 + minute * 60_000
        expected = paper_job.next_run(datetime.fromtimestamp(after / 1000, UTC))
        assert v.next_slot(int(after)) == int(expected.timestamp() * 1000)


def test_slot_times_counts_six_per_day() -> None:
    start = int(datetime(2026, 10, 9, 22, 24, tzinfo=UTC).timestamp() * 1000)
    assert len(v.slot_times(start, start + 14 * 86_400_000)) == 84


# --- agreement with the runner's own figures --------------------------------------


def test_accounts_match_the_runner_status(run: PaperRun) -> None:
    status = run.runner.status()
    overview = v.overview(_ctx(run))
    assert overview.source.state == "ok"
    for acc in overview.accounts:
        c = status["candidates"][acc.candidate]
        assert acc.equity == pytest.approx(c["equity"], abs=0.01)
        assert acc.cash == pytest.approx(c["cash"], abs=0.01)
        assert acc.trades == c["trades"]
        assert acc.open_positions == len(c["open_positions"])
        assert acc.kill_switch == c["kill_switch"]
        assert acc.max_drawdown == pytest.approx(c["max_drawdown"], abs=1e-3)
    assert sum(a.trades for a in overview.accounts) > 0


def test_performance_per_stream_matches_the_runner(run: PaperRun) -> None:
    status = run.runner.status()
    perf = v.performance(_ctx(run))
    rows = {r.key: r for r in perf.by_stream}
    for cand in ("A", "B"):
        for name, st in status["candidates"][cand]["by_strategy"].items():
            row = rows[name]
            assert row.trades == st["trades"]
            assert row.net == pytest.approx(st["net_pnl"], abs=0.01)
            assert row.profit_factor == pytest.approx(st["profit_factor"], abs=1e-3)
            assert row.avg_r == pytest.approx(st["avg_r"], abs=1e-3)
    assert sum(r.trades for r in perf.by_symbol) == sum(r.trades for r in perf.by_candidate)
    assert perf.criteria.min_trades == DEFAULT_SPEC.min_trades_to_evaluate
    assert {e.candidate for e in perf.equity} == {"A", "B"}
    assert all(e.points == sorted(e.points, key=lambda p: p.ts_ms) for e in perf.equity)


def test_positions_match_the_runner(run: PaperRun) -> None:
    status = run.runner.status()
    view = v.positions(_ctx(run))
    expected = {
        (cand, p["symbol"]) for cand, c in status["candidates"].items() for p in c["open_positions"]
    }
    assert {(p.candidate, p.symbol) for p in view.positions} == expected
    for p in view.positions:
        assert p.risk_at_stop >= 0 and p.notional > 0
        assert p.stream is not None and p.stream.startswith(f"{p.candidate}:")
    assert {r.candidate for r in view.risk} == {"A", "B"}
    for r in view.risk:
        assert {lim.key for lim in r.limits} == {
            "open_risk",
            "gross_exposure",
            "positions",
            "daily_loss",
            "drawdown",
        }


# --- decisions and their reasons ----------------------------------------------------


def test_every_decision_row_is_attributed_to_a_stream(run: PaperRun) -> None:
    page = v.decisions(_ctx(run), limit=500)
    assert page.total > 0
    for d in page.items:
        assert d.stream in {s.id for s in DEFAULT_SPEC.streams}, d
        assert d.candidate == d.stream.split(":")[0]


def test_rejections_fills_and_approvals_carry_their_signal(run: PaperRun) -> None:
    ctx = _ctx(run)
    items = v.decisions(ctx, group="signal", limit=500).items
    approved = [d for d in items if d.kind == "approved"]
    assert approved, "the synthetic market produces entries"
    for d in approved:
        assert d.signal_code in ("breakout_up", "breakout_down", "cross_up", "cross_down")
        assert d.signal_reason
    for d in items:
        if d.kind == "rejected":
            assert d.reason and d.reason_code != "other"


def test_paging_is_stable_and_complete(run: PaperRun) -> None:
    ctx = _ctx(run)
    seen: list[int] = []
    before = None
    while True:
        page = v.decisions(ctx, limit=37, before_id=before)
        seen += [d.id for d in page.items]
        if page.next_before_id is None:
            break
        before = page.next_before_id
    assert seen == sorted(seen, reverse=True) and len(seen) == len(set(seen)) == page.total


def test_filters_by_group_stream_and_reason(run: PaperRun) -> None:
    ctx = _ctx(run)
    page = v.decisions(ctx, group="no_trade", stream="A:ETH-USD", limit=500)
    assert page.items and all(
        d.kind in v.NO_TRADE_KINDS and d.stream == "A:ETH-USD" for d in page.items
    )
    code = page.items[0].reason_code
    narrowed = v.decisions(ctx, group="no_trade", stream="A:ETH-USD", reason_code=code, limit=500)
    assert narrowed.items and all(d.reason_code == code for d in narrowed.items)
    assert sum(c.count for c in page.reason_counts) == page.total


@pytest.mark.parametrize(
    ("kind", "text", "code"),
    [
        (
            "no_signal",
            "no breakout: close 82578.5 inside the 20-bar range 1-2 (40% of it)",
            "no_breakout",
        ),
        (
            "no_signal",
            "no SMA crossover: SMA20 82727 below SMA100 84130.8 since before this bar",
            "no_crossover",
        ),
        ("waiting", "no new closed bar since 2026-10-09T00:00+00:00; next close …", "waiting_bar"),
        ("no_signal", "data: data stale", "data_problem"),
        ("no_signal", "kill switch on: max drawdown", "kill_switch"),
        ("decision", "close broke the 20-bar high", "breakout_up"),
        ("decision", "close broke the 20-bar low", "breakout_down"),
        ("decision", "fast SMA crossed below slow SMA", "cross_down"),
        ("decision", "close fell below the 20-bar low", "channel_exit"),
        ("rejected", "total open risk would exceed the limit", "open_risk_limit"),
        ("rejected", "a position in this instrument is already open", "already_open"),
        ("cancelled", "insufficient liquidity (order above the volume cap)", "liquidity_cap"),
        ("cancelled", "gapped through the stop", "gap_through_stop"),
        ("trade", "stop", "exit_stop"),
        ("trade", "take_profit", "exit_take_profit"),
        ("execution_failed", "venue timeout", "execution_fault"),
        ("no_signal", "something new", "other"),
    ],
)
def test_reason_codes(kind: str, text: str, code: str) -> None:
    assert classify(kind, text) == code
    assert code in CODES


# --- strategies, health, reports ------------------------------------------------------


def test_every_stream_has_a_status(run: PaperRun) -> None:
    view = v.strategies(_ctx(run))
    assert [s.id for s in view.streams] == [s.id for s in DEFAULT_SPEC.streams]
    for s in view.streams:
        assert s.status in {
            "position_open",
            "order_pending",
            "waiting",
            "no_signal",
            "data_problem",
            "kill_switch",
        }
        assert s.last_event is not None and s.last_bar_ms is not None
        assert s.interval == ("1d" if s.id.startswith("A:") else "4h")


def test_health_is_green_on_a_healthy_run(run: PaperRun) -> None:
    ctx = _ctx(run)
    health = v.health(ctx)
    assert health.runs and health.runs[0].ts_ms == run.now[0]
    assert not health.run_overdue
    assert all(not s.stale and s.data_code == "ok" for s in health.streams)
    assert health.request_budget_day == 300
    test = v.overview(ctx).test
    assert test.state == "running" and test.runs_missed == 0
    assert test.runs_done == test.runs_expected == 18 and test.day == 4
    assert v.source(ctx).warnings == []


def test_reports_come_from_the_journal(run: PaperRun) -> None:
    view = v.reports(_ctx(run))
    days = [r.day for r in view.reports]
    assert days == sorted(days, reverse=True) and len(days) >= 3
    assert {c.candidate for c in view.reports[0].candidates} == {"A", "B"}


# --- warnings and test states -------------------------------------------------------


def test_missed_and_overdue_runs_are_flagged(run: PaperRun) -> None:
    later = run.now[0] + 2 * H4 + 40 * 60_000  # two slots passed without a run
    ctx = _ctx(run, later)
    test = v.overview(ctx).test
    assert test.runs_missed == 2
    assert {"run_overdue", "runs_missed"} <= set(v.source(ctx).warnings)
    assert v.health(ctx).run_overdue


def test_stale_data_is_flagged(tmp_path: Path) -> None:
    run = run_paper_test(tmp_path, days=1)
    run.feed.stale.add("A:SOL-USD")
    run.advance(6)
    ctx = _ctx(run)
    assert "data_problem" in v.source(ctx).warnings
    health = {s.id: s for s in v.health(ctx).streams}
    assert health["A:SOL-USD"].data_code == "data_problem"
    assert health["A:SOL-USD"].lag_bars == 1
    streams = {s.id: s for s in v.strategies(ctx).streams}
    assert streams["A:SOL-USD"].status == "data_problem"
    incidents = v.health(ctx).incidents
    assert any(i.reason_code == "data_problem" and i.symbol == "SOL-USD" for i in incidents)


def test_test_states(tmp_path: Path) -> None:
    run = run_paper_test(tmp_path, days=0, warmup_days=30)
    j = run.journal
    assert v.overview(_ctx(run)).test.state == "running"
    j.commit([("job_refused", run.now[0] + 1, "*", {"reason": "code differs"})], {})
    ctx = _ctx(run, run.now[0] + 2)
    assert v.overview(ctx).test.state == "refused" and "job_refused" in v.source(ctx).warnings
    j.commit([("job_disabled", run.now[0] + 3, "*", {"by": "owner"})], {"job": {"enabled": False}})
    assert v.overview(_ctx(run)).test.state == "not_enabled"
    job = enable(j, run.now[0], days=1)
    after = int(job["until_ms"]) + 1
    assert v.overview(_ctx(run, after)).test.state == "finished"


def test_a_journal_without_a_job(tmp_path: Path) -> None:
    run = run_paper_test(tmp_path, days=0, warmup_days=5)
    run.journal.commit([], {"job": {}})
    test = v.overview(_ctx(run)).test
    assert test.state == "not_enabled" and test.runs_expected == 0


def test_a_kill_switch_is_shown(tmp_path: Path) -> None:
    run = run_paper_test(tmp_path, days=0, warmup_days=5)
    acc = run.journal.load("account:B")
    acc["risk"]["kill_switch"] = True
    acc["risk"]["kill_reason"] = "max drawdown reached"
    run.journal.commit([], {"account:B": acc})
    ctx = _ctx(run)
    assert "kill_switch" in v.source(ctx).warnings
    b = next(a for a in v.overview(ctx).accounts if a.candidate == "B")
    assert b.kill_switch and b.kill_reason == "max drawdown reached"
    assert {s.id: s.status for s in v.strategies(ctx).streams}["B:BTC-USD"] == "kill_switch"


def test_a_failed_read_renders_empty_sections(tmp_path: Path) -> None:
    failure = JournalReader(tmp_path / "missing.sqlite").read()
    assert isinstance(failure, ReadFailure)
    ctx = v.context(failure, DEFAULT_SPEC, 1)
    for build in (v.overview, v.strategies, v.positions, v.performance, v.health, v.reports):
        model = build(ctx)
        assert model.source.state == SourceState.MISSING.value
    assert v.decisions(ctx).items == []
    assert v.overview(ctx).accounts == []


def test_kill_switch_notes_without_a_stream_inherit_the_bar(tmp_path: Path) -> None:
    run = run_paper_test(tmp_path, days=0, warmup_days=5)
    ts = run.now[0]
    run.journal.commit(
        [
            ("no_signal", ts, "BTC-USD", {"reason": "kill switch on: manual"}),
            ("bar", ts, "BTC-USD", {"stream": "B:BTC-USD", "equity": 1.0, "close": 1.0}),
        ],
        {},
    )
    item = v.decisions(_ctx(run), group="no_trade", limit=1).items[0]
    assert item.stream == "B:BTC-USD" and item.reason_code == "kill_switch"


def test_equity_points_sit_at_the_bar_close(run: PaperRun) -> None:
    ctx = _ctx(run)
    bars = [r for r in ctx.rows if r.kind == "bar" and r.data.get("stream") == "B:BTC-USD"]
    perf = v.performance(ctx)
    b = next(e for e in perf.equity if e.candidate == "B")
    assert b.points[-1].ts_ms == bars[-1].ts_ms + H4
    a = next(e for e in perf.equity if e.candidate == "A")
    assert all(p.ts_ms % D1 == 0 for p in a.points[1:])
