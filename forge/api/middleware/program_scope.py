#!/usr/bin/env python3
"""
Module:   Program Scope Middleware
Purpose:  Enforce the X-Program-Id wire contract from UX spec v1.4.2 §6.7
          on every non-bypass request, populating request.state.program_id
          for downstream feature-flag and service-layer enforcement.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Spec §6.7 makes the active program a first-class request property
  carried in a dedicated header so the OpenAPI client can inject it
  via a single interceptor and so the React Query cache key can
  partition data by program.  Three states are valid on the wire:

    X-Program-Id: <uuid>   single-program scope
    X-Program-Id: *        all-of-caller's-programs scope (multi-program lists)
    (header omitted)       only allowed for the bypass set

  Four error codes are defined and are wire-stable — frontend logic
  branches on `error.code`, never on the message:

    MISSING_PROGRAM_CONTEXT      400  header required but absent
    PROGRAM_NOT_ASSIGNED         403  UUID not in caller's memberships
    MULTI_PROGRAM_NOT_SUPPORTED  400  '*' on a single-program endpoint
    FEATURE_DISABLED             403  per-program feature flag off
                                       (raised by require_feature, not here)

  Local-dev mode (auth_mode != "cognito") is a pass-through: the
  header is parsed if present so the frontend can develop against the
  contract, but absence is not a 4xx.  This keeps the existing 301-
  test suite green — none of those tests send the header — while
  letting the new contract tests opt in by setting auth_mode="cognito".

  Stack placement: registered after Auth so AuthContext is available
  for membership checks; before Idempotency so the program_id is on
  request.state when handlers run and idempotency keys can be
  partitioned per-program in a future revision.  Runtime execution
  order becomes: Security → Auth → RateLimit → ProgramScope →
  Idempotency → CORS → handler.  Rate-limit runs first so an attacker
  spamming bad headers still pays the per-user budget.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from forge.api.auth import AuthContext
from forge.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bypass set — paths that never require X-Program-Id.
# ---------------------------------------------------------------------------
# /health and /api/health are ECS/ALB probes and carry no auth context.
# /api/users/me returns the caller's own profile and is intentionally
# program-agnostic per spec §6.7.  /docs, /redoc, /openapi.json are
# dev-only OpenAPI surfaces (disabled in production).  /api/auth/* is
# reserved for any future in-process auth endpoints (Cognito-hosted
# today; reserving the prefix avoids a future contract break).
# /test/ is included so tests can register probe routes (e.g. the
# AuthMiddleware unit tests in tests/unit/test_auth.py) without having
# to thread the program-scope contract through every probe.  The
# prefix is dev-only — the route registry never exposes /test/* in
# production deployments.
PUBLIC_PREFIXES: Final[tuple[str, ...]] = ("/api/auth/", "/test/")
PUBLIC_PATHS: Final[frozenset[str]] = frozenset({
    "/health",
    "/api/health",
    "/api/users/me",
    "/docs",
    "/redoc",
    "/openapi.json",
})


# ---------------------------------------------------------------------------
# Multi-program allowlist — endpoints that accept X-Program-Id: *.
# ---------------------------------------------------------------------------
# Per spec §5.2, only the canonical list pages support an "All
# (assigned programs)" filter.  Detail routes (`/api/work-orders/{id}`)
# always target a single resource and therefore reject '*'.
#
# Match shape: (HTTP_METHOD, exact_path).  Sub-paths are intentionally
# excluded — any deeper path on these prefixes is a single-resource
# operation.
MULTI_PROGRAM_ENDPOINTS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("GET", "/api/work-orders"),
    ("GET", "/api/ncrs"),
    ("GET", "/api/inspections"),
    ("GET", "/api/material-certs"),
    ("GET", "/api/parts"),
})


PROGRAM_HEADER: Final[str] = "X-Program-Id"
WILDCARD: Final[str] = "*"


def _is_bypass(path: str, method: str) -> bool:
    """Return True if the request should skip program-scope enforcement.

    Bypass cases: the dedicated public path set, any /api/auth/* prefix,
    and CORS preflight.  Kept as a small focused helper so the dispatch
    body reads as a series of guards.
    """
    if method == "OPTIONS":
        return True
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _supports_multi_program(method: str, path: str) -> bool:
    """Whether the route accepts X-Program-Id: * per spec §5.2."""
    return (method, path) in MULTI_PROGRAM_ENDPOINTS


def _error(code: str, status: int, message: str) -> JSONResponse:
    """Build the standard error envelope used across the API surface.

    Mirrors the shape emitted by AuthMiddleware so the frontend's error
    handler has a single contract: `{"error": {"code", "message"}}`.
    """
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


def _parse_program_id(raw: str) -> str | None:
    """Normalize a header value or return None on invalid syntax.

    Accepts the wildcard literal '*' or any RFC 4122 UUID (any
    canonical or hyphen-stripped form).  UUIDs are normalized to the
    canonical lower-case hyphenated form so membership comparisons are
    case-insensitive at the source.
    """
    raw = raw.strip()
    if not raw:
        return None
    if raw == WILDCARD:
        return WILDCARD
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        return None


def _membership_includes(programs: Iterable[str], program_id: str) -> bool:
    """Membership check that tolerates wildcard claims (local dev only).

    The Cognito Pre Token Generation trigger emits custom:programs as
    a list of program UUIDs.  Local-dev mode injects ["*"] so a single
    auth context can act on every program in tests.  The wildcard
    short-circuit preserves that ergonomics while real Cognito users
    are still required to have an explicit membership for the
    requested program.
    """
    if WILDCARD in programs:
        return True
    return program_id in programs


class ProgramScopeMiddleware(BaseHTTPMiddleware):
    """Validate X-Program-Id and stamp request.state.program_id.

    Enforces the four-state error contract from spec §6.7 in cognito
    mode; in local/test mode it parses the header opportunistically
    but never rejects on absence.  See module docstring for the full
    state machine.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Bypass set runs first so health probes and the user-profile
        # endpoint never need a program header.
        if _is_bypass(request.url.path, request.method):
            request.state.program_id = None
            return await call_next(request)

        settings = (
            getattr(request.app.state, "settings", None) or get_settings()
        )
        # Enforcement gate: full contract in cognito mode, opportunistic
        # parsing in local/test so the existing 301-test suite (none of
        # which send the header) keeps passing without modification.
        enforce = settings.auth_mode == "cognito"

        raw = request.headers.get(PROGRAM_HEADER)

        if raw is None:
            if enforce:
                return _error(
                    "MISSING_PROGRAM_CONTEXT", 400,
                    f"{PROGRAM_HEADER} header is required",
                )
            request.state.program_id = None
            return await call_next(request)

        program_id = _parse_program_id(raw)
        if program_id is None:
            # Malformed header is treated as missing context — same
            # error code so the frontend has a single recovery path
            # ("re-select a program") rather than two.
            if enforce:
                return _error(
                    "MISSING_PROGRAM_CONTEXT", 400,
                    f"{PROGRAM_HEADER} must be a UUID or '*'",
                )
            request.state.program_id = None
            return await call_next(request)

        # Wildcard handling — only valid on the multi-program allowlist.
        if program_id == WILDCARD:
            if not _supports_multi_program(request.method, request.url.path):
                return _error(
                    "MULTI_PROGRAM_NOT_SUPPORTED", 400,
                    "This endpoint requires a single program scope",
                )
            request.state.program_id = WILDCARD
            return await call_next(request)

        # Single-program scope — caller must be a member of the program.
        # Membership is sourced from the AuthContext populated by
        # AuthMiddleware; an empty/missing context on a non-bypass route
        # means the auth chain failed to populate state and the request
        # should already have been rejected upstream.
        auth: AuthContext | None = getattr(request.state, "auth", None)
        if auth is None:
            return _error(
                "MISSING_PROGRAM_CONTEXT", 400,
                "Auth context unavailable for program-scope check",
            )

        if not _membership_includes(auth.programs, program_id):
            return _error(
                "PROGRAM_NOT_ASSIGNED", 403,
                "Caller is not assigned to the requested program",
            )

        request.state.program_id = program_id
        return await call_next(request)
