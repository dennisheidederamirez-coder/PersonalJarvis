"""The independent risk manager: sizing, limits, halts, kill switch, duplicates."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.trading.data import HOUR_MS
from jarvis.trading.engine import DemoTrader
from jarvis.trading.instruments import BTC_USD, ETH_USD, stock
from jarvis.trading.journal import MemoryJournal, SqliteJournal
from jarvis.trading.paper import PaperBroker
from jarvis.trading.risk import (
    RESET_PHRASE,
    Account,
    EntryRequest,
    OpenPosition,
    RiskLimits,
    RiskManager,
)
from jarvis.trading.strategies import Side, Target
from tests.fakes.fake_market import T0, random_walk

ACCOUNT = Account(10_000.0, (), {"BTC-USD": 100.0, "ETH-USD": 100.0})


def _req(
    cid: str = "a",
    side: Side = Side.LONG,
    price: float = 100.0,
    stop: float = 98.0,
    instrument=BTC_USD,
    tp: float | None = None,
) -> EntryRequest:
    return EntryRequest(cid, instrument, side, price, stop, tp)


def test_size_loses_at_most_the_risk_per_trade_at_the_stop() -> None:
    rm = RiskManager()
    verdict = rm.check_entry(_req(stop=98.0), ACCOUNT)
    assert verdict.approved
    assert verdict.qty == pytest.approx(25.0)  # 50 / 2
    assert verdict.risk_amount == pytest.approx(50.0)
    # a wide stop is capped by gross exposure, never leveraged
    wide = RiskManager().check_entry(_req(stop=99.6), ACCOUNT)
    assert wide.approved and wide.qty * 100 <= 10_000 + 1e-6


@pytest.mark.parametrize(
    ("req", "reason"),
    [
        (_req(stop=101.0), "stop on the wrong side of the entry"),
        (_req(side=Side.SHORT, stop=99.0), "stop on the wrong side of the entry"),
        (_req(stop=99.95), "stop too close"),
        (_req(stop=70.0), "stop too far"),
        (_req(price=105.0, stop=103.0), "reference price far from the market"),
        (_req(tp=95.0), "take-profit on the wrong side of the entry"),
        (_req(price=float("nan")), "invalid price"),
        (_req(instrument=stock("AAPL"), stop=98.0), "analysis-only"),
    ],
)
def test_faulty_orders_are_refused(req: EntryRequest, reason: str) -> None:
    verdict = RiskManager().check_entry(req, ACCOUNT)
    assert not verdict.approved
    assert any(reason in r for r in verdict.reasons)


def test_duplicates_are_refused_even_after_a_rejection_and_a_restart(tmp_path: Path) -> None:
    rm = RiskManager()
    assert rm.check_entry(_req("x"), ACCOUNT).approved
    assert rm.check_entry(_req("x"), ACCOUNT).reasons == ("duplicate order",)
    assert not rm.check_entry(_req("bad", stop=101.0), ACCOUNT).approved
    assert rm.check_entry(_req("bad"), ACCOUNT).reasons == ("duplicate order",)

    journal = SqliteJournal(tmp_path / "demo.sqlite")
    journal.save_state(rm.state)
    restarted = RiskManager(state=journal.load_state())
    assert restarted.check_entry(_req("x"), ACCOUNT).reasons == ("duplicate order",)


def test_total_risk_exposure_and_position_count_are_capped() -> None:
    eth = OpenPosition("ETH-USD", Side.LONG, 50.0, 100.0, 98.0, 100.0)  # 100 at risk
    rm = RiskManager(RiskLimits(risk_per_trade=0.005, max_open_risk=0.012))
    assert not rm.check_entry(_req(), Account(10_000.0, [eth], ACCOUNT.marks)).approved
    same = OpenPosition("BTC-USD", Side.LONG, 1.0, 100.0, 98.0, 100.0)
    v = RiskManager().check_entry(_req("b"), Account(10_000.0, [same], ACCOUNT.marks))
    assert "a position in this instrument is already open" in v.reasons
    full = [OpenPosition(f"X{k}", Side.LONG, 1.0, 100.0, 99.0, 100.0) for k in range(3)]
    v = RiskManager().check_entry(_req("c"), Account(10_000.0, full, ACCOUNT.marks))
    assert "maximum number of positions reached" in v.reasons
    big = OpenPosition("ETH-USD", Side.LONG, 95.0, 100.0, 99.9, 100.0)  # 95 % of equity
    v = RiskManager().check_entry(_req("d"), Account(10_000.0, [big], ACCOUNT.marks))
    assert "gross exposure would exceed the limit" in v.reasons


def test_daily_loss_halts_until_the_next_utc_day() -> None:
    rm = RiskManager()
    rm.observe(T0, 10_000)
    assert rm.observe(T0 + HOUR_MS, 9_790) == ["daily_halt"]
    assert "daily loss limit reached" in rm.check_entry(_req("a"), ACCOUNT).reasons
    rm.observe(T0 + 25 * HOUR_MS, 9_800)  # next day
    assert rm.check_entry(_req("b"), ACCOUNT).approved


def test_drawdown_triggers_the_kill_switch_and_only_the_phrase_lifts_it() -> None:
    rm = RiskManager()
    rm.observe(T0, 10_000)
    rm.observe(T0 + 30 * HOUR_MS, 11_000)
    assert rm.observe(T0 + 60 * HOUR_MS, 9_890) == ["kill_switch"]
    assert any("kill switch" in r for r in rm.check_entry(_req("a"), ACCOUNT).reasons)
    assert rm.check_exit("close-1").approved  # reducing risk stays possible
    assert rm.reset_kill("yes") is False and rm.state.kill_switch
    assert rm.reset_kill(RESET_PHRASE) is True
    assert rm.check_entry(_req("b"), ACCOUNT).approved


class OneLong:
    name = "one_long"
    params: dict = {}

    def prepare(self, series):  # noqa: ANN001, ANN201
        return None

    def decide(self, i, current):  # noqa: ANN001, ANN201
        return Target(Side.LONG, 25_000.0, None, "test") if i == 0 else None


def test_the_kill_switch_flattens_every_position_at_the_next_open() -> None:
    series = random_walk(10, seed=3)
    journal = MemoryJournal()
    trader = DemoTrader(PaperBroker(10_000), RiskManager(), journal)
    strategy = OneLong()
    trader.process_bar(series, 0, strategy)
    trader.process_bar(series, 1, strategy)
    assert "BTC-USD" in trader.broker.positions
    trader.risk.kill("manual stop by the owner")
    trader.process_bar(series, 2, strategy)  # queues the flatten
    trader.process_bar(series, 3, strategy)  # executes it at the open
    assert trader.broker.positions == {}
    assert trader.trades[-1].exit_reason == "kill switch"


def test_limits_must_be_sane() -> None:
    with pytest.raises(ValueError):
        RiskLimits(risk_per_trade=0.05, max_open_risk=0.02)
    with pytest.raises(ValueError):
        RiskLimits(max_drawdown=1.5)


def test_eth_is_tradable_and_shorts_are_allowed_for_crypto() -> None:
    v = RiskManager().check_entry(_req("e", Side.SHORT, stop=102.0, instrument=ETH_USD), ACCOUNT)
    assert v.approved
