import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .api import endpoints
from .core.config import settings
from .core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Project Vayu API starting up", extra={"environment": settings.ENVIRONMENT})
    yield
    logger.info("Project Vayu API shutting down")


app = FastAPI(
    title="Project Vayu API",
    description="AI-powered geospatial analysis using Google Earth Engine.",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    try:
        response: Response = await call_next(request)
    except Exception as exc:
        logger.error("unhandled_exception", extra={"path": request.url.path, "error": str(exc)}, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    elapsed = time.perf_counter() - start
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Request-ID": request_id,
        "X-Process-Time": f"{elapsed:.4f}",
    })

    logger.info(
        "http_request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": round(elapsed * 1000, 2),
            "rid": request_id,
        },
    )
    return response


# ── Routes ─────────────────────────────────────────────────────────────────────
app.include_router(endpoints.router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "version": app.version, "environment": settings.ENVIRONMENT}


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "Project Vayu API v1.0.0"}
