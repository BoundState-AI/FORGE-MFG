#!/usr/bin/env python3
"""
Module:   Auth Middleware
Purpose:  Cognito JWT authentication applied to every request.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Cognito authenticates; the application database authorizes (spec §5.0.4).
  This middleware validates the Bearer access token against the Cognito JWKS
  endpoint, extracts custom:roles and custom:programs claims, and populates
  request.state.auth with an AuthContext for downstream permission checks.

  Public paths (/health) bypass auth so ECS/ALB probes work without tokens.

  In local/test mode (auth_mode != "cognito"), the middleware is a
  pass-through so development and testing can proceed without a live
  Cognito user pool.

  Claim sync from admin writes (Cognito attribute update after DB
  role/program change) is live via forge.services.cognito_sync.  The
  30-minute drift window (spec §5.0.4) is a deliberate tradeoff for
  normal actions; zero-drift actions still check live DB roles.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from forge.config import get_settings

from ..auth import build_auth_context, verify_access_token

logger = logging.getLogger(__name__)

# Paths that never require authentication.
# /health is used by ECS task health checks and ALB target group probes.
PUBLIC_PATHS: set[str] = {"/health", "/api/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Cognito access-token validation on every request.

    Sets request.state.auth to an AuthContext on success.
    Returns 401 JSON on auth failure.
    Passes through for public paths, OPTIONS, and local auth mode.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Public endpoints and CORS preflight bypass auth
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Use app-level settings (set by create_app) so test overrides
        # propagate correctly; fall back to the env-loaded singleton.
        settings = getattr(request.app.state, "settings", None) or get_settings()

        # If a test auth override is set on app.state, use it instead
        # of the normal auth flow.  This allows tests to impersonate
        # different users without creating separate app instances.
        auth_override = getattr(request.app.state, "auth_override", None)
        if auth_override is not None:
            request.state.auth = auth_override
            return await call_next(request)

        # In local/test mode, set a fully-permissioned dev auth context
        # so route-level permission checks pass without a Cognito token.
        # The SYSTEM user ID is used as the identity.
        if settings.auth_mode != "cognito":
            from forge.api.auth import ROLE_PERMISSIONS, AuthContext

            all_perms: set[str] = set()
            for perms in ROLE_PERMISSIONS.values():
                all_perms |= perms
            request.state.auth = AuthContext(
                sub="00000000-0000-0000-0000-000000000001",
                username="local-dev",
                roles=["admin", "engineer", "operator", "inspector"],
                programs=["*"],  # wildcard — local dev has all programs
                permissions=frozenset(all_perms),
            )
            return await call_next(request)

        # Extract Bearer token from Authorization header
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "UNAUTHORIZED", "message": "Missing Bearer token"}},
            )

        token = auth_header[7:].strip()
        if not token:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "UNAUTHORIZED", "message": "Empty Bearer token"}},
            )

        # Verify the token against Cognito JWKS
        try:
            claims = await verify_access_token(token, settings)
        except Exception as exc:
            # Why broad catch: Starlette's BaseHTTPMiddleware does not
            # propagate HTTPException as HTTP responses — it re-raises
            # them as uncaught 500 errors.  We must catch all exceptions
            # here and convert to a JSON 401 ourselves.  This is the
            # standard pattern for auth middleware in Starlette (see
            # boundstate_mira CognitoAuthzMiddleware for precedent).
            detail = getattr(exc, "detail", "Authentication failed")
            status = getattr(exc, "status_code", 401)
            logger.warning("Auth failed: %s", detail)
            return JSONResponse(
                status_code=status,
                content={"error": {"code": "UNAUTHORIZED", "message": str(detail)}},
            )

        # Build AuthContext from verified claims and attach to request
        request.state.auth = build_auth_context(claims)

        return await call_next(request)
