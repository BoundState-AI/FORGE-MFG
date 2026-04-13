#!/usr/bin/env python3
"""
Module:   Rate Limit Middleware
Purpose:  Application-level per-user rate limiting per spec §5.15.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §5.15 defines application-level rate limits that supplement WAF
  fleet-wide limits.  Limits are per-user, scoped by auth context sub.

  | Endpoint Group    | Limit | Window    |
  |-------------------|-------|-----------|
  | /api/chat/*       | 20    | 1 minute  |
  | /api/admin/*      | 60    | 1 minute  |
  | All other         | 120   | 1 minute  |

  Rate-limited responses return 429 with Retry-After header (seconds).

  This is an in-process sliding-window counter using a dict with
  timestamps.  Suitable for single-instance and testing.  Production
  should use Redis for cross-instance coordination.
"""

from __future__ import annotations

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from forge.config import get_settings

# Window size in seconds
WINDOW_SECONDS = 60

# Per-endpoint-group limits (spec §5.15)
CHAT_LIMIT = 20
ADMIN_LIMIT = 60
DEFAULT_LIMIT = 120

# In-memory store: user_key -> list of request timestamps
_request_log: dict[str, list[float]] = defaultdict(list)


def _get_limit(path: str) -> int:
    """Determine the rate limit for a given request path.

    Spec §5.15 rate limit groups:
    - /api/chat/*  -> 20 req/min (chat is expensive and advisory-only)
    - /api/admin/* -> 60 req/min (admin endpoints are low-volume)
    - everything else -> 120 req/min
    """
    if path.startswith("/api/chat"):
        return CHAT_LIMIT
    if path.startswith("/api/admin"):
        return ADMIN_LIMIT
    return DEFAULT_LIMIT


def _get_user_key(request: Request) -> str:
    """Extract a per-user key for rate limiting.

    Uses auth context sub if available, falls back to client IP.
    """
    auth = getattr(request.state, "auth", None)
    if auth is not None and hasattr(auth, "sub"):
        return f"user:{auth.sub}"
    if request.client:
        return f"ip:{request.client.host}"
    return "anon"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user sliding-window rate limiter."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        settings = (
            getattr(request.app.state, "settings", None) or get_settings()
        )

        # Skip rate limiting in local/test mode unless explicitly opted in.
        # Tests set app.state.enable_rate_limit = True to exercise limiting.
        if (
            settings.env in ("local", "test")
            and not getattr(request.app.state, "enable_rate_limit", False)
        ):
            return await call_next(request)

        path = request.url.path
        limit = _get_limit(path)
        user_key = _get_user_key(request)
        now = time.monotonic()
        window_start = now - WINDOW_SECONDS

        # Prune old entries and count recent requests
        log = _request_log[user_key]
        _request_log[user_key] = [
            ts for ts in log if ts > window_start
        ]
        log = _request_log[user_key]

        if len(log) >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests",
                    }
                },
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )

        log.append(now)
        return await call_next(request)
