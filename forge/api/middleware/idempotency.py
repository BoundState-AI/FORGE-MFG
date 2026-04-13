#!/usr/bin/env python3
"""
Module:   Idempotency Middleware
Purpose:  Enforce Idempotency-Key on all mutating POST endpoints.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  All mutating POST endpoints require an Idempotency-Key header to prevent
  duplicate writes from network retries (spec §5.15).  The key is scoped to
  the authenticated user (via request.state.auth.sub) with a 24-hour TTL.

  Replay rules (spec §5.15):
  - Same key + same request hash -> return cached response.
  - Same key + different request hash -> 409 Conflict.
  - Missing key on mutating POST -> 400 Bad Request.

  Expired records (>24h) are treated as if they don't exist -- a fresh
  execution occurs.  This is consistent with the spec's 24h TTL.

  The middleware runs after auth (so request.state.auth is available)
  but before route handlers.

  Critical safety invariant: the handler is called at most once per
  request.  If idempotency storage fails after the handler ran, the
  original response is still returned -- the record simply won't be
  cached for future replays.  The handler is NEVER called a second time.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from forge.db import get_session
from forge.models.idempotency import IdempotencyRecord

logger = logging.getLogger(__name__)

# Paths exempt from idempotency enforcement.
EXEMPT_PATHS: set[str] = {"/health", "/api/health"}

# TTL for idempotency records (spec §5.15)
IDEMPOTENCY_TTL = timedelta(hours=24)

# Sentinel user_id for unauthenticated requests (local/dev mode only).
# Security note: in local auth mode, all requests share this single
# idempotency namespace.  This is acceptable because local mode is
# never used in staging or production (enforced by config validation).
_LOCAL_SENTINEL_USER = _uuid.UUID("00000000-0000-0000-0000-000000000000")


def _extract_user_id(request: Request) -> _uuid.UUID:
    """Extract the authenticated user's UUID for idempotency scoping."""
    auth_ctx = getattr(request.state, "auth", None)
    if auth_ctx is not None:
        try:
            return _uuid.UUID(auth_ctx.sub)
        except (ValueError, AttributeError):
            pass
    return _LOCAL_SENTINEL_USER


async def _read_response_body(response: Response) -> bytes:
    """Read the full body from a StreamingResponse."""
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:  # type: ignore[attr-defined]
        if isinstance(chunk, str):
            chunks.append(chunk.encode("utf-8"))
        else:
            chunks.append(chunk)
    return b"".join(chunks)


def _serialize_response(
    response: Response, body: bytes
) -> dict[str, object]:
    """Serialize a response to a JSON-storable dict for cache.

    Preserves status code, headers, media type, and raw body (base64
    encoded) so replays reconstruct the original response exactly.
    """
    # Capture response headers (exclude hop-by-hop / transfer headers)
    headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in {"content-length", "transfer-encoding"}
    }
    return {
        "status_code": response.status_code,
        "headers": headers,
        "media_type": response.media_type,
        "body_b64": base64.b64encode(body).decode("ascii"),
    }


def _deserialize_response(cached: dict[str, object]) -> Response:
    """Reconstruct a Response from cached data.

    Returns a response with the same status, headers, media type,
    and body as the original, preserving fidelity for non-JSON
    responses.
    """
    body = base64.b64decode(str(cached.get("body_b64", "")))
    headers_raw = cached.get("headers")
    headers = dict(headers_raw) if isinstance(headers_raw, dict) else {}
    media_type = cached.get("media_type")

    resp = Response(
        content=body,
        status_code=int(str(cached.get("status_code", 200))),
        media_type=str(media_type) if media_type else None,
    )
    for k, v in headers.items():
        resp.headers[str(k)] = str(v)
    return resp


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Idempotency-Key enforcement for mutating POST endpoints."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method != "POST":
            return await call_next(request)

        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        idem_key = request.headers.get("idempotency-key")
        if not idem_key:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "MISSING_IDEMPOTENCY_KEY",
                        "message": "Idempotency-Key header is required "
                        "on all mutating POST endpoints",
                    }
                },
            )

        user_id = _extract_user_id(request)
        body = await request.body()
        request_hash = hashlib.sha256(body).hexdigest()

        # Look up existing record before executing the handler.
        # If DB is unreachable, fall through to execute without caching
        # rather than double-executing or returning a 500.
        try:
            async for session in get_session():
                cached = await self._lookup(
                    session, idem_key, user_id, request_hash,
                )
                if cached is not None:
                    return cached

                # No cache hit — execute handler exactly once
                response = await call_next(request)
                resp_body = await _read_response_body(response)

                # Best-effort store.  If this fails, the response is
                # still returned — the record won't be cached for
                # future replays, but the handler is NOT re-executed.
                try:
                    await self._store(
                        session, idem_key, user_id,
                        request_hash, response, resp_body,
                    )
                except Exception:
                    logger.exception(
                        "Failed to store idempotency record for key=%s",
                        idem_key,
                    )

                return Response(
                    content=resp_body,
                    status_code=response.status_code,
                    media_type=response.media_type,
                    headers=dict(response.headers),
                )
        except Exception:
            logger.exception("Idempotency DB unavailable")
            # In production/staging, refuse the request rather than
            # executing without replay protection — duplicate side
            # effects on mutating endpoints are worse than downtime.
            # In local/dev mode, fall through so development isn't
            # blocked by a missing database.
            settings = getattr(request.app.state, "settings", None)
            env = getattr(settings, "env", "local") if settings else "local"
            if env in ("production", "staging"):
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "code": "IDEMPOTENCY_UNAVAILABLE",
                            "message": "Idempotency service temporarily "
                            "unavailable — retry later",
                        }
                    },
                )
            return await call_next(request)

        return await call_next(request)  # pragma: no cover

    async def _lookup(
        self,
        session: AsyncSession,
        idem_key: str,
        user_id: _uuid.UUID,
        request_hash: str,
    ) -> Response | None:
        """Check for existing record.  Returns cached response or None."""
        now = datetime.now(UTC)

        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == idem_key,
            IdempotencyRecord.user_id == user_id,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is None:
            return None

        if existing.expires_at < now:
            await session.delete(existing)
            await session.commit()
            return None

        if existing.request_hash == request_hash:
            return _deserialize_response(existing.response_body)

        # Same key + different body -> 409
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "Idempotency-Key already used "
                    "with a different request body",
                }
            },
        )

    async def _store(
        self,
        session: AsyncSession,
        idem_key: str,
        user_id: _uuid.UUID,
        request_hash: str,
        response: Response,
        resp_body: bytes,
    ) -> None:
        """Persist an idempotency record for future replay."""
        now = datetime.now(UTC)
        record = IdempotencyRecord(
            idempotency_key=idem_key,
            user_id=user_id,
            request_hash=request_hash,
            response_status=response.status_code,
            response_body=_serialize_response(response, resp_body),
            created_at=now,
            expires_at=now + IDEMPOTENCY_TTL,
        )
        session.add(record)
        await session.commit()
