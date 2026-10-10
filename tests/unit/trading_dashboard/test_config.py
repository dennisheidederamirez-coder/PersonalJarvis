"""The dashboard's one setting is written through the config writer and read by the routes."""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

from jarvis.core.config import JarvisConfig
from jarvis.core.config_writer import set_trading_dashboard_journal_path
from jarvis.ui.web.trading_routes import journal_path


def _request(cfg: object) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))


def test_the_setter_writes_the_section_and_keeps_the_rest(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[ui]\ntheme = "dark"\n', encoding="utf-8")
    set_trading_dashboard_journal_path("/data/paper.sqlite", path=toml)
    data = tomllib.loads(toml.read_text(encoding="utf-8"))
    assert data["trading_dashboard"]["journal_path"] == "/data/paper.sqlite"
    assert data["ui"]["theme"] == "dark"
    cfg = JarvisConfig.model_validate(data)
    assert journal_path(_request(cfg)) == Path("/data/paper.sqlite")  # type: ignore[arg-type]


def test_empty_means_the_default_under_the_data_dir(tmp_path: Path) -> None:
    cfg = JarvisConfig()
    cfg.memory.data_dir = str(tmp_path)
    assert journal_path(_request(cfg)) == tmp_path / "trading" / "paper_journal.sqlite"  # type: ignore[arg-type]
