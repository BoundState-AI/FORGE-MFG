#!/usr/bin/env python3
"""
Module:   App
Purpose:  FastAPI application factory — builds the FORGE-MFG API with
          middleware, lifespan management, and system health endpoint.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  The app factory pattern (create_app) allows tests to inject alternate
  settings without touching module-level state.  Middleware is layered
  outermost-to-innermost: security headers → auth → idempotency → CORS.
  Interactive docs are disabled in production to prevent information leakage
  in GovCloud (spec §9.3).
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forge.config import Settings, get_settings

from .middleware.auth import AuthMiddleware
from .middleware.idempotency import IdempotencyMiddleware
from .middleware.security import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle.

    Why lifespan instead of on_event: FastAPI recommends the lifespan
    context manager for resource management.  Future slices will
    initialize the async DB engine and validate Cognito JWKS here.
    """
    settings = get_settings()
    logger.warning(
        "FORGE-MFG starting | env=%s debug=%s auth=%s",
        settings.env,
        settings.debug,
        settings.auth_mode,
    )
    yield
    logger.warning("FORGE-MFG shutting down")


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

    # --- Middleware stack ---
    # Starlette applies middleware in reverse registration order: the
    # last-added middleware runs first.  We register innermost-first so
    # the runtime order is: Security → Auth → Idempotency → CORS → app.
    # This guarantees security headers are set on every response, including
    # auth/idempotency short-circuits.
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

    # --- System health endpoint ---
    # Dual-mounted at /health (ECS healthcheck) and /api/health (ALB probe).
    @app.get("/health", tags=["system"])
    @app.get("/api/health", tags=["system"], include_in_schema=False)
    async def health() -> dict[str, str]:
        """Return service health status.

        This endpoint is public (no auth required) so ECS and ALB
        health probes can reach it without credentials.
        """
        return {
            "status": "ok",
            "service": settings.app_name,
            "version": "0.1.0",
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    # --- Domain routers ---
    # Registered here in subsequent slices as each domain lands:
    # app.include_router(work_orders.router, prefix="/api", tags=["work-orders"])

    return app


# Module-level app instance for uvicorn/gunicorn: `forge.api.app:app`
app = create_app()
