"""
app/main.py
FastAPI application entry point.

Handles:
  - Application lifecycle (startup: init DB + graph, shutdown: cleanup)
  - Router registration
  - CORS, logging, and global exception handling
  - Health check endpoint
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router as runs_router
from app.config import settings
from app.db.database import create_all_tables, dispose_engine
from app.db.models import Run  # noqa: F401 — ensures model is registered with metadata
from app.graph.graph import close_graph, init_graph

# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs startup tasks before the app starts accepting requests,
    and shutdown tasks after it stops.
    """
    # ---- STARTUP -----------------------------------------------------------
    logger.info("=" * 60)
    logger.info("  Workflow Orchestrator starting up...")
    logger.info(f"  Environment : {settings.app_env}")
    logger.info(f"  LLM Model   : {settings.nvidia_model}")
    logger.info(f"  Mock LLM    : {settings.use_mock_llm}")
    logger.info("=" * 60)

    # 1. Create SQLAlchemy ORM tables (runs table, etc.)
    logger.info("Creating database tables...")
    await create_all_tables()
    logger.info("Database tables ready.")

    # 2. Initialise LangGraph + AsyncPostgresSaver (creates checkpoint tables)
    logger.info("Initialising LangGraph workflow engine...")
    await init_graph()
    logger.info("LangGraph workflow engine ready.")

    logger.info("Application startup complete — ready to accept requests.")

    yield  # ← application runs here

    # ---- SHUTDOWN ----------------------------------------------------------
    logger.info("Application shutting down...")
    await close_graph()
    await dispose_engine()
    logger.info("Cleanup complete. Goodbye.")


# ---------------------------------------------------------------------------
# FastAPI application instance
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Customer Support Issue Triage Orchestrator",
    description=(
        "Durable, resumable multi-step agent workflow for customer support ticket triage. "
        "Powered by LangGraph, Nvidia Nemotron, FastAPI, and PostgreSQL."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Process-Time-Ms"] = str(duration_ms)
    return response


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled exception on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_server_error",
            "detail": str(exc) if settings.is_development else "An unexpected error occurred.",
            "path": str(request.url.path),
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(runs_router)


# ---------------------------------------------------------------------------
# Health & info endpoints
# ---------------------------------------------------------------------------
@app.get("/", tags=["Health"], summary="API root")
async def root() -> dict[str, str]:
    return {
        "service": "Customer Support Issue Triage Orchestrator",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Health"], summary="Health check")
async def health_check() -> dict[str, Any]:
    """
    Returns service health status. Checks database and graph availability.
    """
    from app.db.database import engine
    from app.graph.graph import _compiled_graph

    checks: dict[str, Any] = {}

    # DB check
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    # Graph check
    checks["graph"] = "ok" if _compiled_graph is not None else "not initialised"

    overall = "healthy" if all(v == "ok" for v in checks.values()) else "degraded"

    return {
        "status": overall,
        "environment": settings.app_env,
        "llm_model": settings.nvidia_model,
        "mock_llm": settings.use_mock_llm,
        "checks": checks,
    }


@app.get("/config", tags=["Health"], summary="Non-sensitive config info")
async def config_info() -> dict[str, Any]:
    """Return non-sensitive runtime configuration for debugging."""
    return {
        "app_env": settings.app_env,
        "log_level": settings.log_level,
        "llm_model": settings.nvidia_model,
        "llm_base_url": settings.nvidia_base_url,
        "use_mock_llm": settings.use_mock_llm,
        "max_retry_attempts": settings.max_retry_attempts,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
    }
