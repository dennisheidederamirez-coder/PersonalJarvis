"""REST routes for the paper-trading dashboard — read-only.

Endpoints::

    GET /api/trading/overview      The 14-day test, virtual accounts, PnL
    GET /api/trading/strategies    Every pre-registered stream and its state
    GET /api/trading/positions     Open positions, queued orders, limit use
    GET /api/trading/decisions     Signals, decisions and their reasons (paged)
    GET /api/trading/performance   Results per strategy, account and market
    GET /api/trading/health        Data quality, job runs, request budget
    GET /api/trading/reports       The daily reports the job wrote

    GET /api/trading/live/overview        LIVE: balance, totals, P/L over time
    GET /api/trading/live/positions       LIVE: open and closed positions (paged)
    GET /api/trading/live/trades          LIVE: fills (paged)
    GET /api/trading/live/costs           LIVE: fees and funding per month and market
    GET /api/trading/live/reconciliation  LIVE: differences against itself, a journal, the balance
    GET /api/trading/live/sources         LIVE: syncs, imports, coverage and gaps

Wired in by the WebServer in ``_build_app()``::

    from .trading_routes import router as trading_router
    app.include_router(trading_router)

There is deliberately no POST, PUT or DELETE: the dashboard cannot start,
stop, trade, sync, import or configure anything. It reads the paper job's
journal through ``jarvis.trading_dashboard.reader`` and the LIVE archive
through ``jarvis.trading_dashboard.live_reader`` — two different files, two
readers, two sets of models (read-only SQLite URIs, short transactions, a
bounded busy wait) — and answers 200 with ``source.state`` explaining a
missing, busy or unreadable file instead of failing.

Loopback-only (the server binds to 127.0.0.1) — no auth token needed.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from jarvis.trading.paper_spec import DEFAULT_SPEC
from jarvis.trading_dashboard import live_views, views
from jarvis.trading_dashboard import schema as s
from jarvis.trading_dashboard.live_reader import LiveArchiveReader
from jarvis.trading_dashboard.reader import JournalReader

router = APIRouter(prefix="/api/trading", tags=["trading"])

#: Default journal file under the instance's data dir.
DEFAULT_JOURNAL = Path("trading") / "paper_journal.sqlite"
#: Default LIVE archive under the instance's data dir.
DEFAULT_LIVE_ARCHIVE = Path("trading") / "live_history.sqlite"

_readers: dict[str, JournalReader] = {}
_live_readers: dict[str, LiveArchiveReader] = {}
_readers_lock = threading.Lock()


def journal_path(request: Request) -> Path:
    """The configured journal, or the default under the data dir."""
    cfg = getattr(request.app.state, "config", None)
    configured = getattr(getattr(cfg, "trading_dashboard", None), "journal_path", "") or ""
    if configured:
        return Path(configured).expanduser()
    data_dir = getattr(getattr(cfg, "memory", None), "data_dir", None) or "data"
    return Path(data_dir).expanduser() / DEFAULT_JOURNAL


def live_archive_path(request: Request) -> Path:
    """The configured LIVE archive, or the default under the data dir."""
    cfg = getattr(request.app.state, "config", None)
    configured = getattr(getattr(cfg, "trading_dashboard", None), "live_archive_path", "") or ""
    if configured:
        return Path(configured).expanduser()
    data_dir = getattr(getattr(cfg, "memory", None), "data_dir", None) or "data"
    return Path(data_dir).expanduser() / DEFAULT_LIVE_ARCHIVE


def _live_ctx(request: Request) -> live_views.LiveContext:
    key = str(live_archive_path(request))
    with _readers_lock:
        reader = _live_readers.get(key)
        if reader is None:
            reader = _live_readers[key] = LiveArchiveReader(Path(key))
    return live_views.context(reader.read(), int(time.time() * 1000))


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


# ---------------------------------------------------------------------- LIVE

_Symbol = Annotated[str | None, Query(max_length=40, pattern=r"^[A-Z0-9]+$")]


@router.get("/live/overview", response_model=s.LiveOverview, summary="LIVE balance and totals")
def get_live_overview(request: Request) -> s.LiveOverview:
    """The real account from the LIVE archive: balance, totals, P/L by day."""
    return live_views.overview(_live_ctx(request))


@router.get(
    "/live/positions", response_model=s.LivePositionsView, summary="LIVE open and closed positions"
)
def get_live_positions(
    request: Request,
    symbol: _Symbol = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.LivePositionsView:
    """Open positions and closed positions (newest close first) with fees and funding."""
    return live_views.positions(_live_ctx(request), symbol=symbol, limit=limit, offset=offset)


@router.get("/live/trades", response_model=s.LiveTradesView, summary="LIVE fills")
def get_live_trades(
    request: Request,
    symbol: _Symbol = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.LiveTradesView:
    """Every archived fill, newest first."""
    return live_views.trades(_live_ctx(request), symbol=symbol, limit=limit, offset=offset)


@router.get("/live/costs", response_model=s.LiveCostsView, summary="LIVE fees and funding")
def get_live_costs(request: Request) -> s.LiveCostsView:
    """Fees and funding per month and per market, and the maker/taker split."""
    return live_views.costs(_live_ctx(request))


@router.get(
    "/live/reconciliation",
    response_model=s.LiveReconciliationView,
    summary="LIVE differences found by reconciliation",
)
def get_live_reconciliation(
    request: Request,
    journal: Annotated[str | None, Query(max_length=40, pattern=r"^[A-Za-z0-9_.\-]+$")] = None,
) -> s.LiveReconciliationView:
    """Venue-internal, journal and balance checks with every difference found."""
    return live_views.reconciliation(_live_ctx(request), journal=journal)


@router.get("/live/sources", response_model=s.LiveSourcesView, summary="LIVE syncs and imports")
def get_live_sources(request: Request) -> s.LiveSourcesView:
    """Sync and import runs, import issues, coverage and the gaps a backfill must fill."""
    return live_views.sources(_live_ctx(request))


__all__ = [
    "DEFAULT_JOURNAL",
    "DEFAULT_LIVE_ARCHIVE",
    "journal_path",
    "live_archive_path",
    "router",
]
