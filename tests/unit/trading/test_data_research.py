"""Data quality, provenance, indicators, research snapshot, metrics — and the
guard that keeps every real exchange and network client out of the package."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from jarvis.trading import indicators as ind
from jarvis.trading.data import HOUR_MS, CsvBarSource, assess, make_series
from jarvis.trading.instruments import BTC_USD, AssetClass, stock
from jarvis.trading.metrics import max_drawdown, profit_factor
from jarvis.trading.research import PENDING_SOURCES, technical_snapshot
from tests.fakes.fake_market import T0, random_walk

PACKAGE = Path(__file__).resolve().parents[3] / "jarvis" / "trading"


def _rows(n: int, *, skip: set[int] | None = None):  # noqa: ANN202
    return [
        (T0 + k * HOUR_MS, 100.0, 101.0, 99.0, 100.0, 5.0)
        for k in range(n)
        if k not in (skip or set())
    ]


def test_quality_grades_gaps_duplicates_and_broken_bars() -> None:
    good = assess(make_series(BTC_USD, HOUR_MS, _rows(100), source="s"))
    assert good.grade == "good" and good.gaps == 0 and good.source == "s"
    gappy = assess(make_series(BTC_USD, HOUR_MS, _rows(100, skip=set(range(10, 30))), source="s"))
    assert gappy.gaps == 20 and gappy.grade == "poor"
    rows = _rows(10)
    rows[3] = (rows[3][0], 100.0, 99.0, 101.0, 100.0, 5.0)  # high < low
    assert assess(make_series(BTC_USD, HOUR_MS, rows, source="s")).grade == "unusable"
    rows = _rows(10)
    rows[4], rows[5] = rows[5], rows[4]
    assert assess(make_series(BTC_USD, HOUR_MS, rows, source="s")).out_of_order == 1


def test_csv_source_keeps_provenance(tmp_path: Path) -> None:
    path = tmp_path / "btc.csv"
    path.write_text(
        "ts,open,high,low,close,volume\n"
        + "\n".join(f"{T0 + k * HOUR_MS},100,101,99,100,5" for k in range(5)),
        encoding="utf-8",
    )
    series = CsvBarSource(path).bars(BTC_USD, HOUR_MS)
    assert len(series) == 5 and series.source == "csv:btc.csv" and series.retrieved_at


def test_indicators_match_hand_values_and_wait_for_history() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    assert np.isnan(ind.sma(x, 3)[1]) and ind.sma(x, 3)[4] == pytest.approx(4.0)
    assert ind.ema(x, 3)[2] == pytest.approx(2.0) and ind.ema(x, 3)[3] == pytest.approx(3.0)
    up = np.arange(1.0, 30.0)
    assert ind.rsi(up, 14)[-1] == pytest.approx(100.0)
    assert ind.prior_high(x, 2)[4] == pytest.approx(4.0)  # excludes the current bar
    h, lo, c = x + 1, x - 1, x
    assert ind.atr(h, lo, c, 3)[-1] == pytest.approx(2.0)


def test_snapshot_findings_carry_source_time_and_quality() -> None:
    snap = technical_snapshot(random_walk(24 * 60, seed=2))
    assert snap.as_of and snap.quality["grade"] in {"good", "fair"}
    keys = {f.key for f in snap.findings}
    assert {
        "last_price",
        "change_24h",
        "quote_volume_24h",
        "realized_vol_30d",
        "atr_pct",
        "rsi_14",
        "trend",
        "range_20d_position",
    } <= keys
    assert all(f.source == "fake:random_walk:2" and f.as_of == snap.as_of for f in snap.findings)
    assert set(snap.missing) == set(PENDING_SOURCES)  # not guessed, named as missing


def test_stocks_are_analysable_but_not_demo_tradable() -> None:
    aapl = stock("aapl")
    assert aapl.asset_class is AssetClass.STOCK and not aapl.demo_tradable
    snap = technical_snapshot(random_walk(24 * 40, seed=3, instrument=aapl))
    assert snap.symbol == "AAPL" and snap.get("last_price")


def test_metrics() -> None:
    assert profit_factor([10.0, -5.0, 5.0]) == pytest.approx(3.0)
    assert profit_factor([]) == 0.0
    assert max_drawdown(np.array([100.0, 120.0, 90.0, 130.0])) == pytest.approx(0.25)


FORBIDDEN_IMPORTS = {
    # network, exchanges, credentials
    "httpx",
    "requests",
    "aiohttp",
    "websockets",
    "socket",
    "ccxt",
    "urllib.request",
    "http.client",
    "jarvis.core.secrets",
    "keyring",
    "jarvis.core.http_pool",
    "jarvis.market_data",  # data arrives as BarSeries; the network layer stays outside
    # models: a trade decision must come from tested rules, never from an AI opinion
    "anthropic",
    "openai",
    "jarvis.brain",
    "jarvis.missions",
    "jarvis.society",
}


def test_the_package_has_no_network_exchange_credential_or_model_path() -> None:
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN_IMPORTS), (
                    f"{path.name} imports {name}"
                )
        text = path.read_text(encoding="utf-8").lower()
        assert "get_secret" not in text and "api_key" not in text, path.name
