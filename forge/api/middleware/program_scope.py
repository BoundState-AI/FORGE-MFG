#!/usr/bin/env python3
"""
Module:   Program Scope Middleware
Purpose:  Enforce the X-Program-Id wire contract from UX spec v1.5.1 §6.7
          on every non-bypass request, populating request.state.program_id
          for downstream feature-flag and service-layer enforcement.

BoundState AI 2026
Version: 0.2.0
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

  Two R5 exceptions to the basic contract:

  - Admin namespace bypass (§6.7, R5-R2-ISSUE-008): /api/admin/* is
    program-agnostic and exempt from the header entirely.  Admin
    routes manage cross-program identity/configuration; the header
    would be meaningless on them.  Authorization for admin routes is
    the route's `Depends(require_permission("admin:manage"))`.

  - Entity-detail GET exception (§6.7.4): GET /api/<resource>/<uuid>
    skips the header membership comparison so a deep-link auto-switch
    (header carries the user's currently-selected program; entity
    lives in a different assigned program) reaches the service layer.
    Authorization there checks entity.program_id ∈ memberships and
    returns 404-not-403 on cross-program (§3.2 existence-leak rule).

  Admin role implicitly satisfies any membership check (§6.7.2,
  R5-R1-ISSUE-011) so admins do not require explicit memberships.

  Local-dev mode (auth_mode != "cognito") is a pass-through: the
  header is parsed if present so the frontend can develop against the
  contract, but absence is not a 4xx.  This keeps the pre-existing
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
# /api/admin/ is program-agnostic per UX spec v1.5.1 §6.7 (R5-R2-ISSUE-008):
# admin endpoints manage cross-program identity and configuration and the
# header would be meaningless on them.  /api/audit/ is in the same bucket —
# but for a different reason per surface: the cross-entity admin reads
# (QA full viewer, integrity panel) span programs by definition, while the
# entity-scoped reads (WO/NCR Audit Trail tabs per §6.6.3) resolve the
# entity's program directly via the entity's own program-scoped getter
# (the URL carries the entity_id, so X-Program-Id adds nothing).  Either
# way an X-Program-Id header would be meaningless, so the bypass holds.
# Authorization for each path is enforced by `audit_service.list_events`:
# admin:manage for cross-entity, entity-type read permission + resolved
# entity scope for entity-scoped.
PUBLIC_PREFIXES: Final[tuple[str, ...]] = (
    "/api/auth/",
    "/api/admin/",
    "/api/audit/",
    "/test/",
)
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


# ---------------------------------------------------------------------------
# Entity-detail GET exception (UX spec v1.5.1 §6.7.4).
# ---------------------------------------------------------------------------
# Spec §6.7.4: "Entity-detail GET endpoints — those that fetch a single
# entity by its ID from the URL — authorize by checking whether the
# entity's program_id is in the caller's program_memberships, not by
# checking whether X-Program-Id matches the entity's program."
#
# Rationale: a deep-link to an entity in a non-current program (with
# the auto-switch UX in §3.2) sends the user's CURRENT selector as the
# header, which won't match the entity's program.  The header still
# stamps request.state.program_id for logging and audit, but the
# membership comparison against the header is skipped — service-layer
# entity.program_id checks become the authorization gate (with 404 on
# cross-program per §3.2 to prevent existence leaks).
#
# Match shape: GET request whose path is exactly
#   /api/<resource>/<uuid-segment>
# where <resource> is an enumerated entity-detail surface and there
# are no further path segments.  Sub-paths (`/audit`, `/traceability`,
# `/operations`) are intentionally excluded — they may carry their own
# authorization rules.
ENTITY_DETAIL_RESOURCES: Final[frozenset[str]] = frozenset({
    "work-orders",
    "operations",
    "ncrs",
    "parts",
    "material-certs",
    "documents",
    "calendars",
    "schedules",
    "inspections",
})


PROGRAM_HEADER: Final[str] = "X-Program-Id"
WILDCARD: Final[str] = "*"
ADMIN_ROLE: Final[str] = "admin"


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


def _is_entity_detail_get(method: str, path: str) -> bool:
    """Whether the request is a §6.7.4 entity-detail GET.

    Entity-detail GETs match exactly `GET /api/<resource>/<uuid>` with
    no further path segments and a known resource name, OR the
    operation events SSE surface `GET /api/operations/<uuid>/events`
    (UX spec v1.5.1 §6.5.2) — both are keyed on a single entity and
    authorize against entity.program_id in the service layer, so the
    header-based membership comparison is skipped and 404 returns on
    cross-program per the §3.2 existence-leak rule.
    """
    if method != "GET":
        return False
    parts = path.strip("/").split("/")
    if parts[:1] != ["api"]:
        return False
    # Canonical `/api/<resource>/<uuid>` shape.
    if len(parts) == 3 and parts[1] in ENTITY_DETAIL_RESOURCES:
        try:
            uuid.UUID(parts[2])
        except (ValueError, AttributeError):
            return False
        return True
    # Operation events sub-path: `/api/operations/<uuid>/events`.
    # The SSE channel is entity-keyed (op_id) and the service-layer
    # scope check runs inside the stream opener, so the same §6.7.4
    # exemption applies as the plain entity-detail GET.
    if (
        len(parts) == 4
        and parts[1] == "operations"
        and parts[3] == "events"
    ):
        try:
            uuid.UUID(parts[2])
        except (ValueError, AttributeError):
            return False
        return True
    return False


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


def _membership_includes(
    auth: AuthContext, program_id: str,
) -> bool:
    """Membership check honoring wildcard claims and the admin exemption.

    Three accept paths:

    1. Admin role — UX spec v1.5.1 §6.7.2 (R5-R1-ISSUE-011): "Users with
       the admin role are implicitly authorized for all programs."  No
       explicit membership entry is required.
    2. Wildcard claim ("*" in custom:programs) — local-dev path so a
       single auth context can act on every program in tests.
    3. Explicit membership — the requested UUID appears in the caller's
       custom:programs claim.
    """
    if ADMIN_ROLE in auth.roles:
        return True
    if WILDCARD in auth.programs:
        return True
    return program_id in auth.programs


def _authorize_parsed_program(
    request: Request, program_id: str, auth: AuthContext | None,
) -> JSONResponse | None:
    """Authorize a successfully-parsed X-Program-Id value.

    Returns a JSONResponse on rejection (the dispatcher returns it
    directly) or None on success (the dispatcher stamps state and
    proceeds).  Split out from `dispatch` so each function stays
    under the Tier-1 ~50-line guideline while preserving the linear
    "guard chain" reading order that matches the spec §6.7 contract.
    """
    # Wildcard scope — only valid on the multi-program allowlist (§5.2).
    if program_id == WILDCARD:
        if not _supports_multi_program(request.method, request.url.path):
            return _error(
                "MULTI_PROGRAM_NOT_SUPPORTED", 400,
                "This endpoint requires a single program scope",
            )
        request.state.program_id = WILDCARD
        return None

    # Single-program scope: AuthContext must already be populated by
    # AuthMiddleware.  Its absence on a non-bypass route signals an
    # auth-chain misorder that should already have been a 401 upstream;
    # treat as MISSING_PROGRAM_CONTEXT defensively rather than 500.
    if auth is None:
        return _error(
            "MISSING_PROGRAM_CONTEXT", 400,
            "Auth context unavailable for program-scope check",
        )

    # §6.7.4 entity-detail exemption: stamp the header for logging/audit
    # but skip header-vs-membership comparison so deep-link auto-switch
    # (current selector ≠ entity's program, both assigned) reaches the
    # service layer where entity.program_id authorization is enforced.
    if _is_entity_detail_get(request.method, request.url.path):
        request.state.program_id = program_id
        return None

    if not _membership_includes(auth, program_id):
        return _error(
            "PROGRAM_NOT_ASSIGNED", 403,
            "Caller is not assigned to the requested program",
        )

    request.state.program_id = program_id
    return None


class ProgramScopeMiddleware(BaseHTTPMiddleware):
    """Validate X-Program-Id and stamp request.state.program_id.

    Enforces the four-state error contract from spec §6.7 in cognito
    mode; in local/test mode it parses the header opportunistically
    but never rejects on absence.  See module docstring for the full
    state machine, the admin-namespace bypass, and the §6.7.4
    entity-detail GET exception.
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
        # parsing in local/test so the pre-existing test suite (none of
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

        auth: AuthContext | None = getattr(request.state, "auth", None)
        rejection = _authorize_parsed_program(request, program_id, auth)
        if rejection is not None:
            return rejection
        return await call_next(request)
