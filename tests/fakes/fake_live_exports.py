"""Synthetic CSV exports for the live-history import tests and the dashboard preview.

All rows come from a ``SimulatedVenue`` — no real account data. Two venue
styles exist because the real export format is not known yet:

- ``api``: the API's own field names, comma-separated, epoch milliseconds,
  realized P/L excluding fees and funding (gross);
- ``localized``: what a spreadsheet in a German locale produces —
  semicolons, decimal commas, ``dd.mm.yyyy HH:MM:SS`` in Europe/Berlin
  without an offset, fees shown as negative debits, P/L after fees and
  funding (net).

``journal_csv`` writes an external journal's export of the same trades with
optional, named perturbations, so every reconciliation code has a cause.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.live_history.simulated_venue import SimulatedVenue

BERLIN = ZoneInfo("Europe/Berlin")


def _dec(x: object) -> Decimal:
    return Decimal(str(x))


def _comma(x: Decimal) -> str:
    return format(x.normalize(), "f").replace(".", ",")


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=BERLIN).strftime("%d.%m.%Y %H:%M:%S")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def venue_positions_csv(venue: SimulatedVenue, path: Path, *, style: str = "api") -> Path:
    rows = sorted(venue.history, key=lambda p: int(p["ctime"]))
    with path.open("w", newline="", encoding="utf-8") as fh:
        if style == "api":
            w = csv.writer(fh)
            w.writerow(
                [
                    "positionId",
                    "symbol",
                    "side",
                    "maxQty",
                    "entryPrice",
                    "closePrice",
                    "fee",
                    "funding",
                    "realizedPNL",
                    "leverage",
                    "marginMode",
                    "ctime",
                    "mtime",
                ]
            )
            for p in rows:
                w.writerow(
                    [
                        p["positionId"],
                        p["symbol"],
                        p["side"],
                        p["maxQty"],
                        p["entryPrice"],
                        p["closePrice"],
                        p["fee"],
                        p["funding"],
                        p["realizedPNL"],
                        p["leverage"],
                        p["marginMode"],
                        p["ctime"],
                        p["mtime"],
                    ]
                )
        elif style == "localized":
            w = csv.writer(fh, delimiter=";")
            w.writerow(
                [
                    "Position ID",
                    "Futures",
                    "Direction",
                    "Size",
                    "Avg. Open Price",
                    "Avg. Close Price",
                    "Trading Fee",
                    "Funding Fee",
                    "Realized PnL",
                    "Open Time",
                    "Close Time",
                ]
            )
            for p in rows:
                fee, funding = _dec(p["fee"]), _dec(p["funding"])
                net = _dec(p["realizedPNL"]) - fee + funding
                w.writerow(
                    [
                        p["positionId"],
                        f"{p['symbol'][:-4]}/USDT",
                        "Long" if p["side"] == "LONG" else "Short",
                        _comma(_dec(p["maxQty"])),
                        _comma(_dec(p["entryPrice"])),
                        _comma(_dec(p["closePrice"])),
                        _comma(-fee),
                        _comma(funding),
                        _comma(net),
                        _local(int(p["ctime"])),
                        _local(int(p["mtime"])),
                    ]
                )
        else:
            raise ValueError(style)
    return path


def venue_fills_csv(venue: SimulatedVenue, path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "tradeId",
                "orderId",
                "positionId",
                "symbol",
                "side",
                "qty",
                "price",
                "fee",
                "realizedPNL",
                "roleType",
                "ctime",
            ]
        )
        for t in sorted(venue.trades, key=lambda t: (int(t["ctime"]), t["tradeId"])):
            w.writerow(
                [
                    t["tradeId"],
                    t["orderId"],
                    t["positionId"],
                    t["symbol"],
                    t["side"],
                    t["qty"],
                    t["price"],
                    t["fee"],
                    t["realizedPNL"],
                    t["roleType"],
                    t["ctime"],
                ]
            )
    return path


@dataclass
class JournalChanges:
    """Named differences a journal export may have against the venue."""

    #: Position ids the journal never recorded (→ missing_in_journal).
    omit: set[str] = field(default_factory=set)
    #: An extra trade the venue does not know (→ missing_at_venue).
    extra: bool = False
    #: Position id → fee the journal shows instead (→ fee_mismatch).
    fee: dict[str, Decimal] = field(default_factory=dict)
    #: Position id → amount added to the journal's P/L (→ pnl_mismatch).
    pnl_offset: dict[str, Decimal] = field(default_factory=dict)
    #: Position id → funding the journal shows instead (→ funding_mismatch).
    funding: dict[str, Decimal] = field(default_factory=dict)
    #: Position ids written twice (→ duplicate_in_file on import).
    duplicate: set[str] = field(default_factory=set)


def journal_csv(venue: SimulatedVenue, path: Path, changes: JournalChanges | None = None) -> Path:
    """The journal's view of every closed position: net P/L, ISO times in UTC."""
    ch = changes or JournalChanges()
    rows: list[list[str]] = []
    for n, p in enumerate(sorted(venue.history, key=lambda p: int(p["ctime"])), start=1):
        pid = str(p["positionId"])
        if pid in ch.omit:
            continue
        fee = ch.fee.get(pid, _dec(p["fee"]))
        funding = ch.funding.get(pid, _dec(p["funding"]))
        net = _dec(p["realizedPNL"]) - _dec(p["fee"]) + _dec(p["funding"])
        net += ch.pnl_offset.get(pid, Decimal(0))
        row = [
            f"J-{n:04d}",
            f"{p['symbol'][:-4]}/USDT",
            str(p["side"]).lower(),
            str(p["maxQty"]),
            str(p["entryPrice"]),
            str(p["closePrice"]),
            _iso(int(p["ctime"])),
            _iso(int(p["mtime"])),
            format(fee, "f"),
            format(funding, "f"),
            format(net, "f"),
        ]
        rows.append(row)
        if pid in ch.duplicate:
            rows.append(list(row))
    if ch.extra:
        opened = venue.now_ms - 40 * 86_400_000
        rows.append(
            [
                "J-9999",
                "SOL/USDT",
                "short",
                "2",
                "151.2",
                "149.9",
                _iso(opened),
                _iso(opened + 3_600_000),
                "0.36",
                "0",
                "2.24",
            ]
        )
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "Trade ID",
                "Pair",
                "Direction",
                "Size",
                "Entry",
                "Exit",
                "Open Date",
                "Close Date",
                "Fees",
                "Funding",
                "Net PnL",
            ]
        )
        w.writerows(rows)
    return path


__all__ = ["JournalChanges", "journal_csv", "venue_fills_csv", "venue_positions_csv"]
