"""REST routes for the paper-trading dashboard — read-only.

Endpoints::

    GET /api/trading/overview      The 14-day test, virtual accounts, PnL
    GET /api/trading/strategies    Every pre-registered stream and its state
    GET /api/trading/positions     Open positions, queued orders, limit use
    GET /api/trading/decisions     Signals, decisions and their reasons (paged)
    GET /api/trading/performance   Results per strategy, account and market
    GET /api/trading/health        Data quality, job runs, request budget
    GET /api/trading/reports       The daily reports the job wrote

Wired in by the WebServer in ``_build_app()``::

    from .trading_routes import router as trading_router
    app.include_router(trading_router)

There is deliberately no POST, PUT or DELETE: the dashboard cannot start,
stop, trade or configure anything. It reads the paper job's journal through
``jarvis.trading_dashboard.reader`` (a read-only SQLite URI, short
transactions, a bounded busy wait) and answers 200 with ``source.state``
explaining a missing, busy or unreadable journal instead of failing.

Loopback-only (the server binds to 127.0.0.1) — no auth token needed.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from jarvis.trading.paper_spec import DEFAULT_SPEC
from jarvis.trading_dashboard import schema as s
from jarvis.trading_dashboard import views
from jarvis.trading_dashboard.reader import JournalReader

router = APIRouter(prefix="/api/trading", tags=["trading"])

#: Default journal file under the instance's data dir.
DEFAULT_JOURNAL = Path("trading") / "paper_journal.sqlite"

_readers: dict[str, JournalReader] = {}
_readers_lock = threading.Lock()


def journal_path(request: Request) -> Path:
    """The configured journal, or the default under the data dir."""
    cfg = getattr(request.app.state, "config", None)
    configured = getattr(getattr(cfg, "trading_dashboard", None), "journal_path", "") or ""
    if configured:
        return Path(configured).expanduser()
    data_dir = getattr(getattr(cfg, "memory", None), "data_dir", None) or "data"
    return Path(data_dir).expanduser() / DEFAULT_JOURNAL


def _reader(path: Path) -> JournalReader:
    key = str(path)
    with _readers_lock:
        reader = _readers.get(key)
        if reader is None:
            reader = _readers[key] = JournalReader(path)
        return reader


def _ctx(request: Request) -> views.DashboardContext:
    path = journal_path(request)
    return views.context(_reader(path).read(), DEFAULT_SPEC, int(time.time() * 1000))


@router.get("/overview", response_model=s.Overview, summary="Paper test status and accounts")
def get_overview(request: Request) -> s.Overview:
    """The 14-day paper test, the virtual accounts and their PnL."""
    return views.overview(_ctx(request))


@router.get("/strategies", response_model=s.StrategiesView, summary="State of every paper strategy")
def get_strategies(request: Request) -> s.StrategiesView:
    """Every pre-registered strategy stream with its latest event."""
    return views.strategies(_ctx(request))


@router.get("/positions", response_model=s.PositionsView, summary="Open positions and risk use")
def get_positions(request: Request) -> s.PositionsView:
    """Open paper positions, queued orders and how much of each limit is used."""
    return views.positions(_ctx(request))


@router.get("/decisions", response_model=s.DecisionPage, summary="Signals and their reasons")
def get_decisions(
    request: Request,
    group: Annotated[Literal["all", "signal", "execution", "no_trade", "risk"], Query()] = "all",
    stream: Annotated[str | None, Query(max_length=40)] = None,
    reason_code: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    since_ms: Annotated[int | None, Query(ge=0)] = None,
) -> s.DecisionPage:
    """Signals, approvals, rejections, fills and no-trade reasons, newest first."""
    return views.decisions(
        _ctx(request),
        group=group,
        stream=stream,
        reason_code=reason_code,
        limit=limit,
        before_id=before_id,
        since_ms=since_ms,
    )


@router.get(
    "/performance", response_model=s.PerformanceView, summary="Results per strategy and market"
)
def get_performance(request: Request) -> s.PerformanceView:
    """Closed-trade results per stream, account and market, and the equity curves."""
    return views.performance(_ctx(request))


@router.get("/health", response_model=s.HealthView, summary="Data quality and job runs")
def get_health(request: Request) -> s.HealthView:
    """Data freshness per stream, recent job runs, request budget and incidents."""
    return views.health(_ctx(request))


@router.get("/reports", response_model=s.ReportsView, summary="Daily paper reports")
def get_reports(request: Request) -> s.ReportsView:
    """The daily reports the paper job wrote to its journal, newest first."""
    return views.reports(_ctx(request))


__all__ = ["DEFAULT_JOURNAL", "journal_path", "router"]
