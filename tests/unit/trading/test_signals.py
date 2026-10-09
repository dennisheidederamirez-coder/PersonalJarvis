"""External (TradingView) signals: strict parsing, dedup, and an honest test
of whether they carry information beyond random timing."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from jarvis.trading.data import HOUR_MS, make_series
from jarvis.trading.indicators import daily_open, pvsra
from jarvis.trading.instruments import BTC_USD
from jarvis.trading.signal_eval import study
from jarvis.trading.signals import ExternalSignal, SignalError, SignalLog, parse_tradingview
from tests.fakes.fake_market import T0, random_walk

NOW = T0 + 10 * HOUR_MS


def _payload(**kw: Any) -> dict[str, Any]:
    base = {
        "v": 1,
        "source": "tradingview",
        "indicator": "PVSRA",
        "signal": "climax_bull",
        "direction": "long",
        "symbol": "btcusdt",
        "exchange": "EXCHANGE",
        "interval": "60",
        "bar_time": "2026-01-01T09:00:00Z",
        "fired_at": "2026-01-01T09:59:58Z",
        "price": 30_100.5,
        "token": "never-stored",
    }
    base.update(kw)
    return base


def test_a_well_formed_alert_becomes_a_signal_without_the_token() -> None:
    s = parse_tradingview(_payload(), received_at_ms=NOW)
    assert (s.symbol, s.interval, s.direction) == ("BTCUSDT", "60", "long")
    assert s.bar_time_ms == T0 + 9 * HOUR_MS and s.latency_ms > 0
    assert "token" not in s.to_dict() and "never-stored" not in str(s.to_dict())


@pytest.mark.parametrize(
    "bad",
    [
        {"v": 2},
        {"direction": "buy"},
        {"price": "30000"},
        {"price": -1},
        {"symbol": "<script>"},
        {"interval": "1 hour"},
        {"bar_time": "yesterday"},
        {"fired_at": "2026-01-01T08:00:00Z"},  # before its bar
        {"fired_at": "2026-03-01T00:00:00Z"},  # far in the future
        {"source": "somewhere"},
    ],
)
def test_malformed_alerts_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(SignalError):
        parse_tradingview(_payload(**bad), received_at_ms=NOW)


def test_a_repeated_delivery_is_one_signal() -> None:
    log = SignalLog()
    a = parse_tradingview(_payload(), received_at_ms=NOW)
    b = parse_tradingview(_payload(fired_at="2026-01-01T09:59:59Z"), received_at_ms=NOW + 5)
    assert log.add(a) is True and log.add(b) is False  # same bar, same alert
    other = parse_tradingview(_payload(signal="climax_bear", direction="short"), received_at_ms=NOW)
    assert log.add(other) is True and len(log.all()) == 2


def _signals(series, idx, direction="long", price_shift=1.0):  # noqa: ANN001, ANN202
    return [
        ExternalSignal(
            "tradingview",
            "X",
            "x",
            direction,
            "BTCUSD",
            "T",
            "60",
            int(series.ts[k]),
            int(series.ts[k]) + HOUR_MS,
            int(series.ts[k]) + HOUR_MS + 500,
            float(series.close[k]) * price_shift,
        )
        for k in idx
    ]


def test_random_signals_are_not_informative() -> None:
    series = random_walk(3000, seed=9)
    rng = np.random.default_rng(1)
    idx = sorted(rng.choice(np.arange(10, 2900), 80, replace=False))
    result = study("random", _signals(series, idx), series, draws=500)
    assert not result.informative and result.matched == 80


def test_signals_that_precede_real_moves_are_recognised() -> None:
    series = random_walk(3000, seed=9)
    fwd = series.close[np.arange(11, 2925) + 4] / series.open[np.arange(11, 2925)] - 1
    idx = [k + 10 for k in np.argsort(fwd)[-60:]]  # bars before the biggest 4-bar rises
    result = study("oracle", _signals(series, idx), series, draws=500)
    assert result.informative
    assert next(h for h in result.horizons if h.horizon_bars == 4).hit_rate > 0.9


def test_mismatched_prices_and_unknown_bars_are_counted() -> None:
    series = random_walk(500, seed=2)
    sig = _signals(series, range(20, 60), price_shift=1.05)
    sig.append(
        ExternalSignal(
            "tradingview",
            "X",
            "x",
            "long",
            "BTCUSD",
            "T",
            "60",
            T0 + 99_999 * HOUR_MS,
            T0,
            T0,
            None,
        )
    )
    result = study("mismatch", sig, series, draws=200)
    assert result.price_mismatches == 40 and result.unmatched == 1
    assert any("disagree" in r for r in result.reasons)


def test_pvsra_classes_and_daily_open() -> None:
    n = 14
    o = np.full(n, 100.0)
    c = np.full(n, 100.5)
    h, lo = c + 0.5, o - 0.5
    v = np.full(n, 10.0)
    v[11], v[12], v[13] = 25.0, 18.0, 16.0  # avg before bar 12 is 11.5 -> 18 is >= 150 %
    c[13] = 99.0  # bear candle
    lo[13] = 98.5
    classes = pvsra(o, h, lo, c, v)
    assert list(classes[:10]) == [0] * 10  # not enough history yet
    assert classes[11] == 2 and classes[12] == 1
    assert classes[13] == -2  # bear; its spread x volume is the largest
    rows = [(T0 + k * HOUR_MS, 100.0 + k, 101.0 + k, 99.0 + k, 100.0 + k, 1.0) for k in range(30)]
    s = make_series(BTC_USD, HOUR_MS, rows, source="t")
    d = daily_open(s.ts, s.open)
    assert d[0] == 100.0 and d[23] == 100.0 and d[24] == 124.0
