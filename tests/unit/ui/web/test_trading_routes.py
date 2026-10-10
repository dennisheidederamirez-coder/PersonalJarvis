"""The ``/api/trading`` surface: read-only, always 200, honest about its source."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes.fake_paper_journal import PaperRun, run_paper_test

ENDPOINTS = (
    "overview",
    "strategies",
    "positions",
    "decisions",
    "performance",
    "health",
    "reports",
)


def _client(data_dir: Path, journal_path: str = "") -> TestClient:
    from jarvis.ui.web.trading_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(data_dir)),
        trading_dashboard=SimpleNamespace(journal_path=journal_path),
    )
    return TestClient(app)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> PaperRun:
    return run_paper_test(tmp_path_factory.mktemp("paper"), days=2)


def test_the_router_has_only_get_routes() -> None:
    from jarvis.ui.web.trading_routes import router

    methods = {m for route in router.routes for m in getattr(route, "methods", set())}
    assert methods == {"GET"}
    assert {getattr(r, "path", "") for r in router.routes} == {
        f"/api/trading/{e}" for e in ENDPOINTS
    }


def test_every_endpoint_answers_from_the_configured_journal(run: PaperRun, tmp_path: Path) -> None:
    before = hashlib.sha256(run.path.read_bytes()).hexdigest()
    client = _client(tmp_path, str(run.path))
    for name in ENDPOINTS:
        res = client.get(f"/api/trading/{name}")
        assert res.status_code == 200, name
        body = res.json()
        assert body["source"]["state"] == "ok", name
        assert body["source"]["file_name"] == run.path.name
        assert str(run.path.parent) not in res.text  # the full local path is not echoed
    assert hashlib.sha256(run.path.read_bytes()).hexdigest() == before


def test_writes_are_not_allowed(run: PaperRun, tmp_path: Path) -> None:
    client = _client(tmp_path, str(run.path))
    for method in ("post", "put", "patch", "delete"):
        for name in ENDPOINTS:
            assert getattr(client, method)(f"/api/trading/{name}").status_code == 405


def test_the_default_path_lives_under_the_data_dir(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.get("/api/trading/overview").json()
    assert body["source"]["state"] == "missing"
    assert body["source"]["file_name"] == "paper_journal.sqlite"
    assert body["accounts"] == []
    assert not (tmp_path / "trading").exists()  # reading never creates anything


def test_a_journal_in_the_default_place_is_found(run: PaperRun, tmp_path: Path) -> None:
    target = tmp_path / "trading" / "paper_journal.sqlite"
    target.parent.mkdir()
    target.write_bytes(run.path.read_bytes())
    body = _client(tmp_path).get("/api/trading/overview").json()
    assert body["source"]["state"] == "ok" and len(body["accounts"]) == 2


def test_decision_paging_and_validation(run: PaperRun, tmp_path: Path) -> None:
    client = _client(tmp_path, str(run.path))
    first = client.get("/api/trading/decisions", params={"limit": 5}).json()
    assert len(first["items"]) == 5 and first["next_before_id"] is not None
    second = client.get(
        "/api/trading/decisions", params={"limit": 5, "before_id": first["next_before_id"]}
    ).json()
    assert second["items"][0]["id"] < first["items"][-1]["id"]
    assert client.get("/api/trading/decisions", params={"group": "orders"}).status_code == 422
    assert client.get("/api/trading/decisions", params={"limit": 0}).status_code == 422
    narrowed = client.get(
        "/api/trading/decisions", params={"group": "no_trade", "stream": "B:BTC-USD"}
    ).json()
    assert narrowed["items"] and {i["stream"] for i in narrowed["items"]} == {"B:BTC-USD"}
