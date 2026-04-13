#!/usr/bin/env python3
"""
Module:   Auth Middleware
Purpose:  Cognito JWT authentication and per-route permission enforcement.

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
  request.state.user for downstream permission checks.

  Public paths (/health) bypass auth so ECS/ALB probes work without tokens.

  Slice 0A ships the shell only — all requests pass through.  Full JWKS
  validation, claim extraction, and permission gating land in slice 1.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Paths that never require authentication.
# /health is used by ECS task health checks and ALB target group probes.
PUBLIC_PATHS: set[str] = {"/health", "/api/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Cognito access-token validation and authorization.

    Slice 0A: pass-through.  Subsequent slices will:
    1. Fetch JWKS from Cognito and cache with TTL.
    2. Decode and verify the Bearer access token (RS256).
    3. Reject ID tokens (only access tokens accepted, spec §5.0.4).
    4. Extract custom:roles and custom:programs into request.state.user.
    5. Enforce per-route permissions from the §5.0.1 vocabulary.
    6. Apply zero-drift live DB checks for admin actions (spec §5.0.4).
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Public endpoints and CORS preflight bypass auth
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Slice 0A: no enforcement — pass-through
        return await call_next(request)
