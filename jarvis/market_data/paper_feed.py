"""Bars for the paper runner: read from the local cache, cross-checked; and an
explicit, incremental update that fetches only newly closed bars.

``StoreProvider`` never touches the network. ``update()`` does — but only
when called on purpose, for the spec's streams, from the last cached bar to
the last CLOSED bar (a handful of requests per call). Nothing here schedules
itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from jarvis.market_data.adapters import Adapter, Kind
from jarvis.market_data.crosscheck import choose
from jarvis.market_data.store import BarStore
from jarvis.trading.paper_runner import StreamData
from jarvis.trading.paper_spec import INSTRUMENTS, PaperSpec, StreamSpec


@dataclass
class StoreProvider:
    store: BarStore
    max_age_bars: int = 2
    history_bars: int = 400  # enough warm-up for every pre-registered indicator

    def __call__(self, stream: StreamSpec, now_ms: int) -> StreamData:
        ins = INSTRUMENTS[stream.symbol]
        start = now_ms - self.history_bars * stream.interval_ms
        primary = self.store.load(stream.primary_source, ins, stream.interval_ms, start, now_ms)
        backup = (
            self.store.load(stream.backup_source, ins, stream.interval_ms, start, now_ms)
            if stream.backup_source
            else None
        )
        # only closed bars take part in the comparison and the decision
        primary = _closed(primary, now_ms)
        backup = _closed(backup, now_ms) if backup is not None else None
        choice = choose(
            primary if len(primary) else None,
            backup if backup is not None and len(backup) else None,
            now_ms=now_ms,
            max_age_bars=self.max_age_bars,
        )
        if choice.use is None:
            return StreamData(None, False, "; ".join(choice.reasons))
        if choice.use == primary.source:
            funding = dict(self.store.load_funding(stream.primary_source))
            return StreamData(primary, True, choice.reasons[0], funding or None)
        # backup in use: its own venue's funding is not cached -> the cost model's
        # constant applies and the journal shows the series' source
        return StreamData(backup, True, "; ".join(choice.reasons), None)


def _closed(series, now_ms):  # type: ignore[no-untyped-def]
    keep = [k for k in range(len(series)) if int(series.ts[k]) + series.interval_ms <= now_ms]
    return series.slice(0, keep[-1] + 1) if keep else series.slice(0, 0)


@dataclass
class UpdateReport:
    fetched: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


async def update(
    spec: PaperSpec,
    store: BarStore,
    adapters: Mapping[str, Adapter],
    now_ms: int,
    *,
    warmup_bars: int = 400,
) -> UpdateReport:
    """Fetch newly closed bars (and funding) for every stream's two sources."""
    report = UpdateReport()
    for stream in spec.streams:
        ins = INSTRUMENTS[stream.symbol]
        for source in (stream.primary_source, stream.backup_source):
            if not source:
                continue
            venue = source.split(":")[0]
            adapter = adapters.get(venue)
            if adapter is None:
                report.errors[source] = "no adapter configured"
                continue
            last = store.last_ts(source, stream.interval_ms)
            start = (
                last + stream.interval_ms
                if last is not None
                else now_ms - warmup_bars * stream.interval_ms
            )
            end = now_ms - now_ms % stream.interval_ms
            if start >= end:
                continue
            try:
                series = await adapter.bars(ins, Kind.PERP, stream.interval_ms, start, end)
                store.save(series)
                report.fetched[f"{source}@{stream.interval_ms}"] = len(series)
                if source == stream.primary_source:
                    store.save_funding(source, await adapter.funding(ins, start, end))
            except Exception as exc:  # noqa: BLE001 — reported per source, the rest continue
                report.errors[source] = type(exc).__name__
    return report


__all__ = ["StoreProvider", "UpdateReport", "update"]
