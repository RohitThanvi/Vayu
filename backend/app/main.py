"""
main.py — VAYU Intelligence Terminal Backend
Drop-in replacement for the existing main.py.
Adds intel scheduler and WebSocket support alongside existing GEE endpoints.
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .core.rate_limit import limiter

from .core.config import settings
from .core.logging_config import setup_logging as configure_logging
from .api import endpoints
from .api.intel_endpoints import router as intel_router
from .api.agri_endpoints import router as agri_router
from .api.report_endpoints import router as report_router
from .api.layers_endpoints import router as layers_router
from .services.intel.scheduler import get_scheduler
from .services.agri.alert_engine import get_agri_engine
from .services.agri.whatsapp import send_whatsapp_message

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("VAYU Intelligence Terminal starting up")

    # Start intel polling scheduler (USGS, FIRMS, GDELT, ACLED, AIS bridge, OpenSky, commodities)
    scheduler = get_scheduler(
        acled_email=getattr(settings, "ACLED_EMAIL", ""),
        acled_password=getattr(settings, "ACLED_PASSWORD", ""),
        ais_bridge_url=getattr(settings, "AIS_BRIDGE_URL", ""),
        ais_bridge_api_key=getattr(settings, "AIS_BRIDGE_API_KEY", ""),
        opensky_client_id=getattr(settings, "OPENSKY_CLIENT_ID", ""),
        opensky_client_secret=getattr(settings, "OPENSKY_CLIENT_SECRET", ""),
        aqi_api_key=getattr(settings, "AQI_API_KEY", ""),
    )
    await scheduler.start()
    logger.info("Intel scheduler started")

    # Start agri alert engine (watchlist risk scanning + WhatsApp push)
    agri_engine = get_agri_engine(whatsapp_notify=send_whatsapp_message)
    await agri_engine.start()
    logger.info("Agri alert engine started")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await agri_engine.stop()
    await scheduler.stop()
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

# ── Rate limiting ─────────────────────────────────────────────────────────────
# `slowapi` was already in requirements.txt but never actually wired up —
# meaning the query endpoint (which triggers a Groq LLM call, and can
# trigger a GEE computation or a SerpApi web search on top of that) had
# no abuse protection at all. Keyed on remote IP, applied per-route (see
# api/endpoints.py's /query) rather than globally, since read-only
# endpoints like /health or the cached intel feeds don't need the same limit.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(endpoints.router, prefix="/api/v1")
app.include_router(intel_router,     prefix="/api/v1")
app.include_router(agri_router,      prefix="/api/v1")
app.include_router(report_router,    prefix="/api/v1")
app.include_router(layers_router,    prefix="/api/v1")


# ── Access logging ────────────────────────────────────────────────────────────
# uvicorn's own access log is deliberately silenced above (WARNING level) to
# cut noise — which also means client IPs were never being logged anywhere at
# all. This replaces it with a structured equivalent: one JSON log line per
# request via logging_config.JSONFormatter, with the real client IP as a
# proper field (greppable/filterable in Render's log stream), not just
# freeform text. Render terminates TLS and proxies requests, so
# request.client.host would show Render's own proxy address, not the visitor
# — the real IP is in X-Forwarded-For (added by Render's proxy), so that's
# checked first and request.client.host is only the fallback for direct/local
# connections (e.g. running this locally without a proxy in front of it).
#
# Privacy note: an IP address is personal data under most privacy frameworks
# (India's DPDP Act, GDPR for any EU visitors). This logs it as ordinary
# operational/security access logging — the same thing most web servers do
# by default — not as a persistent per-visitor analytics store. Render's own
# log retention policy governs how long these lines are kept.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "unknown")
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info(
        f"{request.method} {request.url.path} {response.status_code}",
        extra={
            "client_ip": client_ip,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/robots.txt", tags=["system"], include_in_schema=False)
async def robots_txt():
    # Separate domain from the frontend (frontend/public/robots.txt covers
    # that one) — this is a pure API, nothing here is meant to be indexed.
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/health", tags=["system"])
async def health():
    from .services.intel.store import intel_store
    from .services.intel.vessel_store import vessel_store
    from .services import gee_client
    stats = intel_store.get_stats()
    vstats = vessel_store.get_stats()
    return {
        "status": "ok",
        "version": "2.0.0",
        "intel_events": stats["current_events"],
        "sources": stats.get("by_source", {}),
        "active_vessels": vstats.get("active_vessels", 0),
        "vessels_by_category": vstats.get("by_category", {}),
        "gee_ready": gee_client.GEE_READY,
        "gee_init_error": gee_client.GEE_INIT_ERROR,
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
