#!/usr/bin/env python3
"""
Module:   App
Purpose:  FastAPI application factory with middleware, lifespan, and health.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The app factory pattern (create_app) allows tests to inject alternate
  settings without touching module-level state.  Middleware registration,
  health endpoint, and lifespan are each extracted into focused functions
  so no single function exceeds ~50 lines.  Interactive docs are disabled
  in production to prevent information leakage in GovCloud (spec §9.3).
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forge.config import Settings, get_settings
from forge.db import dispose_engine, init_engine

from .middleware.auth import AuthMiddleware
from .middleware.idempotency import IdempotencyMiddleware
from .middleware.security import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle.

    Why lifespan over on_event: FastAPI recommends the lifespan context
    manager for deterministic resource acquisition and release.
    """
    settings = get_settings()
    logger.warning(
        "FORGE-MFG starting | env=%s debug=%s auth=%s",
        settings.env,
        settings.debug,
        settings.auth_mode,
    )
    init_engine(settings.database_url)
    logger.info("Database engine initialized")
    yield
    await dispose_engine()
    logger.warning("FORGE-MFG shutting down")


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    """Wire the middleware stack in correct Starlette registration order.

    Starlette applies middleware in reverse registration order: the
    last-added middleware runs first.  We register innermost-first so
    the runtime execution order is:

        Security → Auth → Idempotency → CORS → app

    This guarantees security headers are set on every response, including
    auth/idempotency short-circuit responses (spec §9.3.8).
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)


def _register_health(app: FastAPI, settings: Settings) -> None:
    """Register system health endpoints for ECS and ALB probes.

    Dual-mounted at /health (ECS task healthcheck) and /api/health
    (ALB target group probe).  Both are public — no auth required.
    """

    @app.get("/health", tags=["system"])
    @app.get("/api/health", tags=["system"], include_in_schema=False)
    async def health() -> dict[str, str]:
        """Return service liveness status."""
        return {
            "status": "ok",
            "service": settings.app_name,
            "version": "0.1.0",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: Optional override; defaults to env-loaded singleton.

    Returns:
        Fully wired FastAPI instance ready for uvicorn/gunicorn.
    """
    if settings is None:
        settings = get_settings()

    # Disable interactive docs in production (spec §9.3)
    show_docs = settings.env != "production"

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if show_docs else None,
        redoc_url="/redoc" if show_docs else None,
        openapi_url="/openapi.json" if show_docs else None,
        lifespan=lifespan,
    )

    # Store settings on app.state so middleware can access the same
    # instance passed to create_app (critical for test overrides).
    app.state.settings = settings

    _register_middleware(app, settings)
    _register_health(app, settings)

    # Domain routers registered here as each slice lands:
    # app.include_router(work_orders.router, prefix="/api", tags=["work-orders"])

    return app


# Module-level app instance for uvicorn/gunicorn: `forge.api.app:app`
app = create_app()
