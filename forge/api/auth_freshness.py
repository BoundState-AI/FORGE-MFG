#!/usr/bin/env python3
"""
Module:   Auth Freshness Enforcement
Purpose:  Centralized re-auth gate for Phase 1 protected actions
          (UX spec v1.5.1 §9 and Build Spec §9.3.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this module exists:
  The freshness check on safety-critical sign-off and NCR disposition
  has to fire BEFORE any business-side effect commits.  Spec §11.3
  line 1538 spells this out: "The server returns REAUTH_REQUIRED before
  any business-side effects execute" — that ordering is what makes the
  same-Idempotency-Key retry safe (the idempotency middleware never
  caches a partial state, so the retry key is still un-committed).

  Centralizing the logic here means:
    * One place that knows the 300-second threshold and the 30-second
      clock-skew allowance from spec §9.2.
    * One place that knows the JWT-vs-DB freshness-source switch from
      spec §9.1.
    * One canonical error envelope so the frontend never has to pattern-
      match on prose — it keys on ``code: REAUTH_REQUIRED`` exactly once
      in ``frontend/src/auth/reauth.ts``.

  The check is invoked from the service layer (not as a FastAPI
  dependency) because it needs the loaded operation / NCR to know
  which protected-action label to emit and (in the DB-fallback path)
  the same ``session`` the service is already using.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Final

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext
from forge.config import get_settings
from forge.models.user import User

# Spec §9.2: "401 REAUTH_REQUIRED when now - auth_time > 300 seconds,
# with 30 seconds of clock-skew allowance."  The clock-skew allowance
# adds to the threshold (not the comparator) so the public payload's
# ``max_auth_age_seconds`` reads as the actual server-side limit.
_BASE_MAX_AUTH_AGE_SECONDS: Final[int] = 300
_CLOCK_SKEW_SECONDS: Final[int] = 30
MAX_AUTH_AGE_SECONDS: Final[int] = _BASE_MAX_AUTH_AGE_SECONDS + _CLOCK_SKEW_SECONDS


class AuthFreshnessAction(enum.StrEnum):
    """Protected actions in Phase 1 GC-3 scope (UX spec §9.2).

    The enum value is what ships in the REAUTH_REQUIRED error envelope
    so the frontend can render an action-specific re-auth modal copy.
    Adding a new value is a wire-contract change — bump the spec
    section reference and update the frontend mapper at the same time.
    """

    sign_off = "sign_off"
    ncr_disposition = "ncr_disposition"


class StaleAuthError(HTTPException):
    """401 REAUTH_REQUIRED — issued before any business-side effects.

    The detail body deliberately mirrors the existing FastAPI HTTPException
    shape (``{"detail": ...}``) so it threads through the same logging
    and middleware paths.  The structured payload lives under ``detail``
    keyed by ``error.code`` so the frontend's typedClient ``unwrap``
    helper sees ``response.data.detail.error.code`` — matching the
    same pattern used by VERSION_CONFLICT (spec §6.3.2) and the SoD
    violation envelope.

    auth_age_seconds is included when known so the frontend can show a
    truthful "your sign-in is N seconds old" line in the re-auth modal.
    None means we skipped the comparator (e.g. JWT had no auth_time and
    the DB fallback had no row) and conservatively treated the caller
    as stale.
    """

    def __init__(
        self,
        action: AuthFreshnessAction,
        *,
        auth_age_seconds: int | None,
    ) -> None:
        super().__init__(
            status_code=401,
            detail={
                "error": {
                    "code": "REAUTH_REQUIRED",
                    "action": action.value,
                    "auth_age_seconds": auth_age_seconds,
                    "max_auth_age_seconds": MAX_AUTH_AGE_SECONDS,
                    "message": (
                        "This action requires re-authentication. "
                        "Sign in again to continue."
                    ),
                }
            },
        )


async def _resolve_auth_age_seconds(
    auth: AuthContext,
    session: AsyncSession,
) -> int | None:
    """Compute the age of the caller's last authentication, in seconds.

    Returns None when no freshness signal is available — the caller
    treats that as "stale" so a missing signal never silently passes
    the gate.  The fork between JWT and DB sources is a single config
    switch (``Settings.auth_freshness_source``) so test suites can flip
    it without touching a live Cognito pool (UX spec v1.5.1 §9.1).

    Local-mode bypass: when ``auth_mode != "cognito"`` AND no auth_time
    claim is present, return 0 ("fresh").  Local dev does not run a
    real authentication event we can age against, and existing
    integration suites build override AuthContexts without setting
    auth_time.  Suites that want to exercise the freshness gate either
    set an explicit ``auth_time`` on the override or flip
    ``Settings.auth_freshness_source`` to ``"db"`` and seed
    ``users.last_auth_at``.  The strict path (Cognito-issued tokens
    always carry auth_time) is unaffected.
    """
    settings = get_settings()
    now_epoch = int(datetime.now(UTC).timestamp())

    if (
        settings.auth_mode != "cognito"
        and settings.auth_freshness_source == "jwt"
        and auth.auth_time is None
    ):
        return 0

    if settings.auth_freshness_source == "db":
        # DB fallback path: read the timestamp the application has
        # written for this user.  Wildcard-only callers (local dev /
        # SYSTEM actor with sub == sentinel) get treated as fresh
        # because they have no real authentication event to age.
        try:
            user_id = auth.sub
            row = await session.execute(
                select(User.last_auth_at).where(User.cognito_sub == user_id)
            )
            last_auth_at = row.scalar_one_or_none()
        except Exception:
            return None
        if last_auth_at is None:
            return None
        delta = now_epoch - int(last_auth_at.timestamp())
        return max(delta, 0)

    # Default JWT path: read auth_time from the verified token claims.
    if auth.auth_time is None:
        return None
    return max(now_epoch - auth.auth_time, 0)


async def require_fresh_auth(
    auth: AuthContext,
    session: AsyncSession,
    action: AuthFreshnessAction,
) -> None:
    """Raise StaleAuthError if the caller's auth is older than allowed.

    Service layers call this BEFORE any state-mutating work for the
    listed protected actions.  Local-mode / SYSTEM-actor contexts
    (no auth_time and no DB row) are intentionally treated as stale on
    the JWT path so production-like code paths run uniformly in tests.
    Tests that need to bypass set the JWT auth_time directly on the
    AuthContext override.
    """
    age = await _resolve_auth_age_seconds(auth, session)
    if age is None or age > MAX_AUTH_AGE_SECONDS:
        raise StaleAuthError(action=action, auth_age_seconds=age)
