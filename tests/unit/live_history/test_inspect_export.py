"""``inspect``: an export's format, never its contents; writes nothing; never inside the repo."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from jarvis.live_history import __main__ as cli
from jarvis.live_history.csv_import import ImportKind, ImportOptions
from jarvis.live_history.inspect_export import InsideRepository, inspect_export
from jarvis.live_history.simulated_venue import SimulatedVenue
from tests.fakes.fake_live_exports import journal_csv, venue_positions_csv
from tests.fakes.fake_live_venue import NOW_MS


def _now() -> int:
    return NOW_MS


def _cells(path: Path, delimiter: str) -> set[str]:
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter=delimiter))
    return {c for row in rows[1:] for c in row if c}


def test_the_report_has_the_format_and_none_of_the_contents(tmp_path: Path) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    path = venue_positions_csv(venue, tmp_path / "export.csv", style="localized")
    os.chmod(path, 0o644)
    before = sorted(p.name for p in tmp_path.iterdir())
    r = inspect_export(path, ImportOptions(ImportKind.POSITIONS), now_ms=_now)
    text = json.dumps(r)
    for cell in _cells(path, ";"):
        assert f'"{cell}"' not in text, cell  # no value, id, symbol or time
    assert "Position ID" in r["headers"] and r["mapping"]["fee"] == "Trading Fee"
    assert (r["delimiter"], r["decimal_mark"], r["encoding"]) == ("semicolon", "comma", "utf-8")
    assert r["rows"] == len(venue.history)
    assert r["columns"]["opened"] == {"no_zone": len(venue.history)}
    assert r["columns"]["fee"]["negative"] == len(venue.history)
    assert r["trial_import"]["refused"] == "timezone_required"
    assert {"declare_timezone", "restrict_permissions"} <= set(r["hints"])
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # nothing written

    os.chmod(path, 0o600)
    ok = inspect_export(path, ImportOptions(ImportKind.POSITIONS, tz="Europe/Berlin"), now_ms=_now)
    trial = ok["trial_import"]
    assert trial["status"] == "ok" and trial["would_insert"] == len(venue.history)
    assert (trial["pnl_basis"], trial["pnl_basis_source"], trial["fee_sign_flipped"]) == (
        "net",
        "inferred",
        True,
    )
    assert ok["readable_by_others"] is False and "restrict_permissions" not in ok["hints"]


def test_unknown_columns_are_reported_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "journal.csv"
    path.write_text(
        "Coin;Richtung;Menge\nBTC;long;1\n", encoding="utf-8"
    )  # i18n-allow: a German-labelled sample export
    r = inspect_export(path, ImportOptions(ImportKind.JOURNAL), now_ms=_now)
    assert r["mapping_refused"] == {"code": "missing_column", "field": "symbol"}
    assert set(r["unmapped_headers"]) == {"Coin", "Richtung", "Menge"}  # i18n-allow: same sample
    assert "map_columns" in r["hints"] and "trial_import" not in r


def test_a_file_inside_a_git_tree_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    path = journal_csv(SimulatedVenue(now_ms=NOW_MS), repo / "export.csv")
    with pytest.raises(InsideRepository):
        inspect_export(path, ImportOptions(ImportKind.JOURNAL), now_ms=_now)


def test_cli_inspect_and_real_import_outside_the_repo_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    venue = SimulatedVenue(now_ms=NOW_MS)
    path = venue_positions_csv(venue, tmp_path / "p.csv")
    assert cli.main(["inspect", "--file", str(path), "--kind", "positions"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["trial_import"]["status"] == "ok" and not (tmp_path / "live.sqlite").exists()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    inside = venue_positions_csv(venue, repo / "p.csv")
    db = tmp_path / "real.sqlite"
    args = ["import", "--archive", str(db), "--file", str(inside), "--kind", "positions"]
    assert cli.main([*args, "--origin", "real"]) == 2
    assert "outside the git repository" in capsys.readouterr().out
    assert not db.exists()
    assert cli.main(["inspect", "--file", str(inside), "--kind", "positions"]) == 2
    with pytest.raises(SystemExit):
        cli.main(args)  # --origin is mandatory
