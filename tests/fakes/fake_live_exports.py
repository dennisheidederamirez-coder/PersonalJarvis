"""Synthetic CSV exports for the live-history import tests and the dashboard preview.

All rows come from a ``SimulatedVenue`` — no real account data. Two venue
styles exist because the real export format is not known yet:

- ``api``: the API's own field names, comma-separated, epoch milliseconds,
  realized P/L excluding fees and funding (gross);
- ``localized``: what a spreadsheet in a German locale produces —
  semicolons, decimal commas, ``dd.mm.yyyy HH:MM:SS`` in Europe/Berlin
  without an offset, fees shown as negative debits, P/L after fees and
  funding (net).

``web_positions_csv`` and ``web_fills_csv`` write the venue's WEB export
layout (``Layout.WEB_EXPORT``) with the same header names and cell shapes as
the real one, but synthetic values: no ids, symbol·side·margin mode in one
column, sizes with their unit, ISO times in UTC to the second, the P/L both
gross and net. Named perturbations make a row contradict itself.

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


def _iso_s(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


WEB_POSITION_HEADERS = [
    "futures",
    "opening time",
    "average entry price",
    "average closing price",
    "closed amount",
    "closed value",
    "position pnl",
    "realized pnl",
    "funding fees",
    "position fee",
    "closed time",
]
WEB_FILL_HEADERS = [
    "date",
    "side",
    "coin",
    "futures",
    "transaction amount",
    "average price",
    "realized p/l",
    "netprofits",
    "fee",
]


def web_positions_csv(
    venue: SimulatedVenue,
    path: Path,
    *,
    net_offset: dict[str, Decimal] | None = None,
    fee_offset: dict[str, Decimal] | None = None,
    cross: set[str] | None = None,
) -> Path:
    """Closed positions as the web export shows them.

    ``net_offset``: position id → amount added to the stated net P/L only, so
    that row's net no longer equals gross − fee + funding.
    ``fee_offset``: position id → amount added to the fee (the row stays
    consistent; it then differs from the venue's own record).
    ``cross``: position ids shown with the CROSS margin mode.
    """
    offsets = net_offset or {}
    fees = fee_offset or {}
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(WEB_POSITION_HEADERS)
        for p in sorted(venue.history, key=lambda p: int(p["mtime"]), reverse=True):
            pid = str(p["positionId"])
            gross, funding = _dec(p["realizedPNL"]), _dec(p["funding"])
            fee = _dec(p["fee"]) + fees.get(pid, Decimal(0))
            qty, close = _dec(p["maxQty"]), _dec(p["closePrice"])
            mode = "CROSS" if cross and pid in cross else "ISOLATION"
            side = "Long" if p["side"] == "LONG" else "Short"
            w.writerow(
                [
                    f"{p['symbol']} {side}\u00b7{mode}",
                    _iso_s(int(p["ctime"])),
                    format(_dec(p["entryPrice"]), "f"),
                    format(close, "f"),
                    f"{format(qty, 'f')} {_base(str(p['symbol']))}",
                    format(qty * close, "f"),
                    format(gross, "f"),
                    format(gross - fee + funding + offsets.get(pid, Decimal(0)), "f"),
                    format(funding, "f"),
                    format(fee, "f"),
                    _iso_s(int(p["mtime"])),
                ]
            )
    return path


def web_fills_csv(venue: SimulatedVenue, path: Path, *, repeat: int = 0) -> Path:
    """Every fill as the web export shows it; ``repeat`` writes N rows from the middle
    of the file twice (as a second fill in the same second would look)."""
    sides = {str(p["positionId"]): str(p["side"]) for p in [*venue.history, *venue.pending]}
    rows: list[list[str]] = []
    for t in sorted(venue.trades, key=lambda t: int(t["ctime"]), reverse=True):
        direction = sides.get(str(t["positionId"]), "LONG")
        opening = (t["side"] == "BUY") == (direction == "LONG")
        action = f"{'Open' if opening else 'Close'} {direction.title()}"
        gross, fee = _dec(t["realizedPNL"]), _dec(t["fee"])
        rows.append(
            [
                _iso_s(int(t["ctime"])),
                action,
                "USDT",
                str(t["symbol"]),
                str(t["qty"]),
                str(t["price"]),
                format(gross, "f"),
                format(gross - fee, "f"),
                format(fee, "f"),
            ]
        )
    mid = len(rows) // 2
    rows = [*rows[: mid + repeat], *rows[mid : mid + repeat], *rows[mid + repeat :]]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(WEB_FILL_HEADERS)
        w.writerows(rows)
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


__all__ = [
    "WEB_FILL_HEADERS",
    "WEB_POSITION_HEADERS",
    "JournalChanges",
    "journal_csv",
    "venue_fills_csv",
    "venue_positions_csv",
    "web_fills_csv",
    "web_positions_csv",
]
