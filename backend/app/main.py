"""FastAPI application: REST + (later) WebSocket hub + APScheduler, one process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routers import (
    alerts,
    brief,
    calendar,
    catalog,
    correlation,
    health,
    layouts,
    macro,
    market,
    news,
    notes,
    series,
    statements,
    sys,
    valuation,
    watchlists,
    ws,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.data.catalog.loader import load_seed
from app.data.http import close_client
from app.data.registry import get_registry
from app.data.store.models import Pref
from app.data.store.sqlite import create_all, dispose, init_engine, session_scope
from app.jobs.scheduler import get_scheduler, register_jobs
from app.market import service as market_service
from app.market import stream
from app.search.fts import ensure_fts

log = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


async def _apply_prefs() -> None:
    async with session_scope() as s:
        row = await s.get(Pref, "grey_sources_enabled")
        if row is not None:
            get_registry().grey_enabled = bool(row.value)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    settings.ensure_dirs()
    init_engine(settings.sqlite_url)
    await create_all()
    await load_seed()
    get_registry()
    await _apply_prefs()
    await ensure_fts()
    await watchlists.ensure_default()
    await market_service.load_quotes_from_db()
    await watchlists.sync_stream()
    stream.start()
    if settings.scheduler_enabled:
        sch = register_jobs()
        sch.start()
        log.info("scheduler started with %d jobs", len(sch.get_jobs()))
    log.info("terminal ready on http://%s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        if settings.scheduler_enabled:
            get_scheduler().shutdown(wait=False)
        await stream.stop()
        await close_client()
        await dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Personal Terminal", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(series.router)
    app.include_router(catalog.router)
    app.include_router(sys.router)
    app.include_router(market.router)
    app.include_router(watchlists.router)
    app.include_router(notes.router)
    app.include_router(ws.router)
    app.include_router(layouts.router)
    app.include_router(correlation.router)
    app.include_router(valuation.router)
    app.include_router(alerts.router)
    app.include_router(news.router)
    app.include_router(statements.router)
    app.include_router(calendar.router)
    app.include_router(macro.router)
    app.include_router(brief.router)
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


app = create_app()
