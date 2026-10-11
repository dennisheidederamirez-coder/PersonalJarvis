"""Inspect a real export's FORMAT without revealing or storing its contents.

``python -m jarvis.live_history inspect --file EXPORT.csv --kind positions``
answers the questions the first real import depends on — which columns
exist and how they map, how numbers and times are written, which sign
fees and funding use, which P/L definition the rows follow, and what an
import would reject — and nothing else:

- **No cell content leaves the file.** The report holds the header names
  (the format, not the data), counts, shares and issue codes with line
  numbers. No value, id, symbol or timestamp is printed, logged or stored.
- **Nothing is written.** The trial import runs against an in-memory
  archive that is discarded.
- **Real exports stay out of the repository.** A file inside a git working
  tree is refused, so it cannot be committed by accident.

The report is safe to share for setting up the importer (``--map``,
``--tz``, ``--pnl-basis`` …).
"""

from __future__ import annotations

import csv
import io
import re
import stat
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from jarvis.live_history.archive import LiveArchive, Origin, Outcome, RunStatus
from jarvis.live_history.csv_import import (
    MAX_FILE_BYTES,
    ImportOptions,
    ImportRefused,
    ImportReport,
    _delimiter,
    _NeedTimezone,
    _read_text,
    _RowError,
    import_csv,
    parse_decimal,
    parse_time,
    resolve_columns,
)

#: Rows sampled for the per-column statistics (the trial import reads all).
SAMPLE_ROWS: Final = 5000
#: Issue lines listed in the report (codes are counted in full).
MAX_LISTED_ISSUES: Final = 25
_SAFE_HEADER: Final = re.compile(r"^[\w .()/%#:\-\[\]]{1,64}$", re.UNICODE)
_SIGNED_FIELDS: Final = ("fee", "funding", "pnl")


class InsideRepository(Exception):
    """The file lies inside a git working tree; real exports must not."""


def inside_git_tree(path: Path) -> Path | None:
    """The working tree root that contains ``path``, if any."""
    for parent in path.resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _header(name: str) -> str:
    """A header name as the format's evidence; anything unusual is withheld."""
    name = name.strip()
    return name if _SAFE_HEADER.match(name) else "<withheld>"


def _time_class(raw: str) -> str:
    s = raw.strip()
    if s.isdigit():
        return {13: "epoch_ms", 10: "epoch_s"}.get(len(s), "digits_other")
    try:
        parse_time(s, None)
    except _NeedTimezone:  # classified, never echoed
        return "no_zone"
    except _RowError:  # classified, never echoed
        return "unparsed"
    return "with_zone"


def inspect_export(path: Path, opts: ImportOptions, *, now_ms: Callable[[], int]) -> dict[str, Any]:
    """The format report for one export (see the module docstring)."""
    root = inside_git_tree(path)
    if root is not None:
        raise InsideRepository(
            "the file lies inside a git working tree; move it outside the repository first"
        )
    st = path.stat()
    out: dict[str, Any] = {
        "kind": opts.kind.value,
        "size_bytes": st.st_size,
        "readable_by_others": bool(st.st_mode & (stat.S_IROTH | stat.S_IRGRP)),
    }
    if st.st_size > MAX_FILE_BYTES:
        out["refused"] = "file_too_large"
        return out
    probe = ImportReport(opts.kind, True, RunStatus.RUNNING, file_sha256="")
    try:
        text = _read_text(path, probe)
    except ImportRefused as exc:  # the refusal code is the report's answer
        out["refused"] = exc.code.value
        return out
    out["sha256_prefix"] = probe.file_sha256[:12]
    out["encoding"] = probe.encoding
    delimiter = _delimiter(text)
    out["delimiter"] = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(
        delimiter, "other"
    )
    decimal_comma = opts.decimal_comma if opts.decimal_comma is not None else delimiter == ";"
    out["decimal_mark"] = "comma" if decimal_comma else "point"

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    headers = next(reader, [])
    out["headers"] = [_header(h) for h in headers]
    try:
        columns = resolve_columns(headers, opts.kind, opts.mapping)
        out["mapping"] = {f: _header(headers[i]) for f, i in sorted(columns.items())}
    except ImportRefused as exc:  # reported as mapping_refused; no trial import then
        out["mapping_refused"] = {"code": exc.code.value, "field": exc.field}
        columns = {}
    mapped = set(columns.values())
    out["unmapped_headers"] = [
        _header(h) for i, h in enumerate(headers) if i not in mapped and h.strip()
    ]

    rows = 0
    stats: dict[str, Counter[str]] = {f: Counter() for f in columns}
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        rows += 1
        if rows > SAMPLE_ROWS:
            continue
        for field, idx in columns.items():
            raw = cells[idx].strip() if idx < len(cells) else ""
            c = stats[field]
            if not raw:
                c["empty"] += 1
                continue
            if field in ("opened", "closed", "time"):
                c[_time_class(raw)] += 1
                continue
            try:
                value = parse_decimal(raw, decimal_comma=decimal_comma)
            except _RowError:  # classified as text, never echoed
                c["text"] += 1
                continue
            c["number"] += 1
            if field in _SIGNED_FIELDS:
                c["negative" if value < 0 else "zero" if value == 0 else "positive"] += 1
    out["rows"] = rows
    out["columns"] = {f: dict(c) for f, c in stats.items()}

    if columns:
        # The trial import: the same checks as a real one, in memory, discarded.
        archive = LiveArchive.in_memory(now_ms=now_ms(), origin=Origin.REAL)
        try:
            report = import_csv(archive, path, opts, now_ms=now_ms, dry_run=True)
        finally:
            archive.close()
        out["trial_import"] = {
            "status": report.status.value,
            "refused": report.refused.value if report.refused else None,
            "would_insert": report.counts.get(Outcome.INSERTED, 0),
            "rejected_rows": report.rejected,
            "duplicates_in_file": report.duplicates_in_file,
            "pnl_basis": report.pnl_basis.value if report.pnl_basis else None,
            "pnl_basis_source": report.pnl_basis_source,
            "fee_sign_flipped": report.fee_sign_flipped,
            "issues": report.by_code(),
            "first_issues": [
                {"line": i.line, "code": i.code.value, "field": i.field}
                for i in report.issues[:MAX_LISTED_ISSUES]
            ],
        }
    out["hints"] = _hints(out)
    return out


def _hints(r: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if r.get("readable_by_others"):
        hints.append("restrict_permissions")  # chmod 600
    refused = (r.get("mapping_refused") or {}).get("code")
    if refused in ("missing_column", "ambiguous_column"):
        hints.append("map_columns")  # --map field=Header
    trial = r.get("trial_import") or {}
    if trial.get("refused") == "timezone_required":
        hints.append("declare_timezone")  # --tz Europe/Berlin
    if trial.get("refused") in ("pnl_basis_required", "net_pnl_without_funding"):
        hints.append("declare_pnl_basis")  # --pnl-basis gross|net_of_fees|net
    if (trial.get("issues") or {}).get("bad_number"):
        hints.append("check_decimal_mark")  # --decimal comma|point
    return hints


__all__ = [
    "MAX_LISTED_ISSUES",
    "SAMPLE_ROWS",
    "InsideRepository",
    "inside_git_tree",
    "inspect_export",
]
