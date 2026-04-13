#!/usr/bin/env python3
"""
Module:   Security Middleware
Purpose:  Attach spec-mandated security headers to every HTTP response.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Security headers are product behavior, not polish (CLAUDE.md build
  philosophy).  The header set matches spec §9.3.8 exactly.  CSP
  connect-src includes GovCloud Cognito and S3 endpoints required for
  presigned uploads.  CORS is handled separately by FastAPI's
  CORSMiddleware in the app factory.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Spec §9.3.8 — every response carries these headers.
SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' "
        "https://cognito-idp.us-gov-west-1.amazonaws.com "
        "https://s3.us-gov-west-1.amazonaws.com "
        "https://*.s3.us-gov-west-1.amazonaws.com"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach required security headers to every response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        return response
