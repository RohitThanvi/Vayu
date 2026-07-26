"""
main.py — VAYU Intelligence Terminal Backend
Drop-in replacement for the existing main.py.
Adds intel scheduler and WebSocket support alongside existing GEE endpoints.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from .core.config import settings
from .core.logging_config import setup_logging as configure_logging
from .api import endpoints
from .api.intel_endpoints import router as intel_router
from .services.intel.scheduler import get_scheduler
from .services.intel.ais_stream import get_ais_client

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("VAYU Intelligence Terminal starting up")

    # Start intel polling scheduler (USGS, FIRMS, GDELT, ACLED)
    scheduler = get_scheduler(
        acled_email=getattr(settings, "ACLED_EMAIL", ""),
        acled_password=getattr(settings, "ACLED_PASSWORD", ""),
    )
    await scheduler.start()
    logger.info("Intel scheduler started")

    # Start AIS maritime vessel tracking (no-op if no API key configured)
    ais_client = get_ais_client(api_key=getattr(settings, "AISSTREAM_API_KEY", ""))
    await ais_client.start()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await scheduler.stop()
    await ais_client.stop()
    logger.info("VAYU Intelligence Terminal shutdown complete")


app = FastAPI(
    title="VAYU Intelligence Terminal",
    description="Unified geospatial OSINT platform — satellite + real-time intelligence",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(endpoints.router, prefix="/api/v1")
app.include_router(intel_router,     prefix="/api/v1")


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health():
    from .services.intel.store import intel_store
    from .services.intel.vessel_store import vessel_store
    stats = intel_store.get_stats()
    vstats = vessel_store.get_stats()
    return {
        "status": "ok",
        "version": "2.0.0",
        "intel_events": stats["current_events"],
        "sources": stats.get("by_source", {}),
        "active_vessels": vstats.get("active_vessels", 0),
        "vessels_by_category": vstats.get("by_category", {}),
    }


@app.middleware("http")
async def log_requests(request, call_next):
    response = await call_next(request)
    logger.info("http_request", extra={
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
    })
    return response
