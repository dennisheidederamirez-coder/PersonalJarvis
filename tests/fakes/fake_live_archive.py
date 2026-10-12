"""A synthetic LIVE archive with a realistic history, for the dashboard tests and preview.

Built only from a ``SimulatedVenue`` — no real account, no network:

1. three syncs a few days apart (three balance observations); between the
   second and the third a deposit lands that no trade explains;
2. the open position closes between the first and the second sync;
3. the venue's position and trade exports are imported for the history older
   than the API's 90-day look-back (one old fill's fee doctored, so the
   venue-internal check has something to find);
4. an import that failed on a few rows, and an external journal with named
   differences (one trade missing, one extra, one fee, one P/L, one funding).

With ``web_exports`` the venue's web exports (no ids) are imported as well:
the position export BEFORE the first sync — so the API later delivers the
same positions with ids and the dashboard pairs the copies — with two rows
whose stated net contradicts their components and one fee that differs from
the API; the fill export after the syncs, as daily totals, with one row
written twice.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from jarvis.live_history.archive import LiveArchive
from jarvis.live_history.csv_import import ImportKind, ImportOptions, Layout, import_csv
from jarvis.live_history.reconcile import PnlBasis
from jarvis.live_history.simulated_venue import DAY_MS, SimulatedVenue
from jarvis.live_history.sync import FIRST_LOOKBACK_MS, SyncService
from tests.fakes.fake_live_exports import (
    JournalChanges,
    journal_csv,
    venue_fills_csv,
    venue_positions_csv,
    web_fills_csv,
    web_positions_csv,
)
from tests.fakes.fake_live_venue import NOW_MS, FakeTime, make_client

DEPOSIT = Decimal("25")


@dataclass(frozen=True)
class DemoArchive:
    path: Path
    now_ms: int
    venue: SimulatedVenue
    #: position ids the journal was given differences for, by finding code
    journal_changes: dict[str, str]
    doctored_fill_position: str
    #: web exports only: position ids by the difference built into them
    web_changes: dict[str, tuple[str, ...]] = field(default_factory=dict)


async def _sync(venue: SimulatedVenue, archive: LiveArchive, at_ms: int) -> None:
    venue.now_ms = at_ms
    await SyncService(
        make_client(venue, FakeTime(), wall_ms=lambda: at_ms),
        archive,
        venue="primary",
        account="main",
        source="simulated",
        now_ms=lambda: at_ms,
    ).run()


#: Built into the web position export (stated net − components; fee added).
WEB_NET_OFFSETS: tuple[Decimal, ...] = (Decimal("0.37"), Decimal("-44.48"))
WEB_FEE_OFFSET = Decimal("0.25")


def build_demo_archive(
    directory: Path, now_ms: int = NOW_MS, *, web_exports: bool = False
) -> DemoArchive:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "live_history.sqlite"
    for f in directory.glob("live_history.sqlite*"):
        f.unlink()
    t0 = now_ms - 6 * DAY_MS
    venue = SimulatedVenue(now_ms=t0, days=150, closed_positions=36)
    archive = LiveArchive.open(path, now_ms=t0, origin="synthetic")
    web_changes: dict[str, tuple[str, ...]] = {}
    try:
        if web_exports:
            # the web export first: the API delivers the same positions later
            closed_now = sorted(venue.history, key=lambda p: int(p["ctime"]))
            recent_ids = [str(p["positionId"]) for p in closed_now[-12:]]
            web_changes = {
                "export_pnl_inconsistent": (recent_ids[1], recent_ids[5]),
                "export_differs_from_api": (recent_ids[3],),
            }
            import_csv(
                archive,
                web_positions_csv(
                    venue,
                    directory / "web_positions.csv",
                    net_offset=dict(
                        zip(web_changes["export_pnl_inconsistent"], WEB_NET_OFFSETS, strict=True)
                    ),
                    fee_offset={recent_ids[3]: WEB_FEE_OFFSET},
                    cross={recent_ids[7]},
                ),
                ImportOptions(ImportKind.POSITIONS, layout=Layout.WEB_EXPORT),
                now_ms=lambda: t0 - 60_000,
            )
        asyncio.run(_sync(venue, archive, t0))
        venue.now_ms = t0 + 2 * DAY_MS
        venue.close_open_position(t0 + DAY_MS, Decimal("61250"))
        asyncio.run(_sync(venue, archive, t0 + 2 * DAY_MS))
        venue.wallet += DEPOSIT  # a transfer in: no trade explains it
        asyncio.run(_sync(venue, archive, t0 + 4 * DAY_MS))

        at = t0 + 5 * DAY_MS
        old_end = t0 - FIRST_LOOKBACK_MS
        since = t0 - 150 * DAY_MS

        # a first try at the trade export with a few broken rows
        fills_path = venue_fills_csv(venue, directory / "venue_trades.csv")
        lines = fills_path.read_text(encoding="utf-8").splitlines()
        broken = list(lines)
        broken[3] = broken[3].rsplit(",", 1)[0] + ",not-a-time"
        broken[5] = ",".join(c if i != 6 else "12.5.3" for i, c in enumerate(broken[5].split(",")))
        broken_path = directory / "venue_trades_broken.csv"
        broken_path.write_text("\n".join(broken) + "\n", encoding="utf-8")
        import_csv(
            archive,
            broken_path,
            ImportOptions(ImportKind.FILLS, pnl_basis=PnlBasis.GROSS),
            now_ms=lambda: at,
        )

        # the clean exports, one old fill's fee doctored
        oldest = sorted(venue.trades, key=lambda t: int(t["ctime"]))
        target = next(t for t in oldest if t["realizedPNL"] != "0")
        fixed = [
            ",".join(
                str(Decimal(c) + Decimal("0.40"))
                if i == 7 and line.startswith(target["tradeId"])
                else c
                for i, c in enumerate(line.split(","))
            )
            for line in lines
        ]
        fills_path.write_text("\n".join(fixed) + "\n", encoding="utf-8")
        import_csv(
            archive,
            fills_path,
            ImportOptions(ImportKind.FILLS, pnl_basis=PnlBasis.GROSS, covers=(since, old_end)),
            now_ms=lambda: at + 60_000,
        )
        import_csv(
            archive,
            venue_positions_csv(venue, directory / "venue_positions.csv", style="localized"),
            ImportOptions(ImportKind.POSITIONS, tz="Europe/Berlin", covers=(since, old_end)),
            now_ms=lambda: at + 120_000,
        )

        closed = sorted(venue.history, key=lambda p: int(p["ctime"]))
        recent = [str(p["positionId"]) for p in closed if int(p["ctime"]) >= t0 - 60 * DAY_MS]
        changes = JournalChanges(
            omit={recent[3]},
            extra=True,
            fee={recent[2]: Decimal("0.95")},
            pnl_offset={recent[4]: Decimal("-2.40")},
            funding={recent[6]: Decimal("0")},
            duplicate={recent[8]},
        )
        # the journal only covers the last 60 days
        journal_venue = SimulatedVenue(now_ms=venue.now_ms, days=1, closed_positions=0)
        journal_venue.history = [p for p in closed if int(p["ctime"]) >= t0 - 60 * DAY_MS]
        journal_venue.now_ms = venue.now_ms
        import_csv(
            archive,
            journal_csv(journal_venue, directory / "journal_export.csv", changes),
            ImportOptions(ImportKind.JOURNAL),
            now_ms=lambda: at + 180_000,
        )
        if web_exports:
            import_csv(
                archive,
                web_fills_csv(venue, directory / "web_fills.csv", repeat=1),
                ImportOptions(ImportKind.FILLS, layout=Layout.WEB_EXPORT),
                now_ms=lambda: at + 240_000,
            )
    finally:
        archive.close()
    return DemoArchive(
        path=path,
        now_ms=now_ms,
        venue=venue,
        journal_changes={
            "missing_in_journal": recent[3],
            "fee_mismatch": recent[2],
            "pnl_mismatch": recent[4],
            "funding_mismatch": recent[6],
        },
        doctored_fill_position=str(target["positionId"]),
        web_changes=web_changes,
    )


__all__ = [
    "DEPOSIT",
    "WEB_FEE_OFFSET",
    "WEB_NET_OFFSETS",
    "DemoArchive",
    "build_demo_archive",
]
