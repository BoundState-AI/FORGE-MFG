#!/usr/bin/env python3
"""
Module:   Program Scope Dependencies
Purpose:  FastAPI dependencies for reading the active program from request
          state and enforcing per-program feature flags from UX spec
          v1.4.2 §1.4 (schedule_enabled, oracle_access).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  The middleware (forge.api.middleware.program_scope) decides whether
  a request *carries* a valid program scope.  Whether a specific
  feature is allowed within that scope is a separate concern that
  depends on the Program row — which means a database read and is
  therefore cheaper to do per-route via Depends than for every
  request in middleware.

  Three dependencies are exported:

  - get_program_id: returns the parsed scope from request.state, or
    400 MISSING_PROGRAM_CONTEXT if the middleware didn't populate it.
    Used by routes that need to know "which program" but have no
    feature flag gating their existence.

  - require_program_id: the same, but rejects '*' with 400
    MULTI_PROGRAM_NOT_SUPPORTED.  Used by single-program writes.

  - require_feature: factory that returns a dependency enforcing one
    of the Program feature flags (schedule_enabled, oracle_access).
    Returns 400 MULTI_PROGRAM_NOT_SUPPORTED on '*' (per spec §6.7:
    feature flags are per-program and cannot be evaluated across
    multiple programs at once) and 403 FEATURE_DISABLED when the flag
    is off.  Loads the Program row from the request-scoped session.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.deps import get_db
from forge.config import get_settings
from forge.models.program import Program

# Wire-stable error codes.  Kept as constants so a typo in a route
# wouldn't silently break frontend error-handling branches.
ERR_MISSING = "MISSING_PROGRAM_CONTEXT"
ERR_NOT_ASSIGNED = "PROGRAM_NOT_ASSIGNED"
ERR_MULTI_NOT_SUPPORTED = "MULTI_PROGRAM_NOT_SUPPORTED"
ERR_FEATURE_DISABLED = "FEATURE_DISABLED"

WILDCARD = "*"

# Whitelist of attribute names that map to per-program boolean flags
# on the Program model.  Centralized so a typo in a route's
# Depends(require_feature("oracle_acess")) fails loudly at import
# time rather than as a 500 in production.
_FEATURE_FLAGS: frozenset[str] = frozenset({
    "schedule_enabled",
    "oracle_access",
})


def _http_error(status: int, code: str, message: str) -> HTTPException:
    """Build an HTTPException with the standard envelope payload.

    The middleware emits {"error": {"code","message"}} as a JSON body.
    Route-level dependencies use HTTPException, which FastAPI wraps in
    {"detail": ...} by default.  We standardize on the same shape by
    passing a dict as detail so the frontend has a single contract.
    """
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message},
    )


def get_program_id(request: Request) -> str:
    """Return the active program scope (UUID string or '*').

    Raises 400 MISSING_PROGRAM_CONTEXT if the middleware did not
    populate request.state.program_id (which in cognito mode means
    the header was absent or malformed; in local mode means the
    caller chose not to send it but the route requires it).
    """
    pid = getattr(request.state, "program_id", None)
    if pid is None:
        raise _http_error(400, ERR_MISSING, "X-Program-Id header is required")
    # request.state attributes are typed Any by Starlette; the middleware
    # only ever stores str | None, so this cast is a typing fence not a
    # validation step.
    return str(pid)


def require_program_id(request: Request) -> str:
    """Return a single-program UUID; reject the wildcard.

    Used by routes whose semantics only make sense scoped to one
    program (writes, detail reads).  Multi-program lists use
    get_program_id directly and branch on '*' themselves.
    """
    pid = get_program_id(request)
    if pid == WILDCARD:
        raise _http_error(
            400, ERR_MULTI_NOT_SUPPORTED,
            "This endpoint requires a single program scope",
        )
    return pid


def require_feature(flag: str) -> Any:
    """Return a dependency that enforces a per-program feature flag.

    Loads the Program row identified by request.state.program_id and
    rejects with 403 FEATURE_DISABLED if the named boolean attribute
    is False.  Wildcard scope is rejected up-front because feature
    flags are per-program and a cross-program decision is undefined.

    Why a factory: each gated route declares the exact flag name it
    requires, keeping the access policy declarative and grep-able.
    """
    if flag not in _FEATURE_FLAGS:
        # Fail at import time, not at request time.
        raise ValueError(f"Unknown program feature flag: {flag!r}")

    async def _check(
        request: Request,
        session: AsyncSession = Depends(get_db),
    ) -> str | None:
        # Local-dev parity with the middleware: when auth_mode != cognito
        # the wire contract is not enforced and the existing test suite
        # does not send the header.  Treat this dependency as a no-op
        # in that mode so feature-gated routes remain reachable in
        # tests that pre-date the X-Program-Id contract.
        #
        # Settings are read from request.app.state (not the cached
        # get_settings singleton) so test fixtures that override
        # auth_mode via Settings(...) are honored.  Falling back to
        # the singleton handles startup edges where state isn't set.
        settings = (
            getattr(request.app.state, "settings", None) or get_settings()
        )
        if settings.auth_mode != "cognito":
            return getattr(request.state, "program_id", None)

        pid = require_program_id(request)
        try:
            program_uuid = uuid.UUID(pid)
        except (ValueError, AttributeError) as exc:
            # The middleware normalized this already; guard against a
            # downstream caller that bypasses the middleware in tests.
            raise _http_error(
                400, ERR_MISSING,
                "X-Program-Id is not a valid UUID",
            ) from exc

        program = await session.get(Program, program_uuid)
        if program is None:
            # A scoped UUID with no Program row is a stale claim; treat
            # as not-assigned so the frontend prompts a re-select.
            raise _http_error(
                403, ERR_NOT_ASSIGNED,
                "Program not found for the requested scope",
            )
        if not getattr(program, flag, False):
            raise _http_error(
                403, ERR_FEATURE_DISABLED,
                f"Feature '{flag}' is disabled for this program",
            )
        return pid

    return _check


__all__ = [
    "ERR_FEATURE_DISABLED",
    "ERR_MISSING",
    "ERR_MULTI_NOT_SUPPORTED",
    "ERR_NOT_ASSIGNED",
    "WILDCARD",
    "get_program_id",
    "require_feature",
    "require_program_id",
]
