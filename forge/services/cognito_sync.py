#!/usr/bin/env python3
"""
Module:   Cognito Sync
Purpose:  Admin-driven Cognito attribute sync and emergency global
          sign-out (spec §5.0.4, §5.13).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Spec §5.0.4 makes the application database the *authorization source
  of truth*, with Cognito serving as authenticator only.  The drift
  budget is 30 minutes — that is the window in which a stale access
  token can still carry yesterday's claims.  To keep the window bounded,
  admin endpoints that change role or program membership call Cognito
  AdminUpdateUserAttributes immediately so newly issued tokens reflect
  the DB.  For deactivation and admin-role removal, we additionally
  call AdminUserGlobalSignOut to invalidate refresh tokens; the stolen
  session can persist at most until the current access token expires.

  This module is the *only* wrapper for boto3 cognito-idp calls.  All
  admin service paths route through it so a future rotation of the
  SDK or a move to an alternate IdP lands in one place.

  Failures surface as CognitoSyncError which the service layer converts
  to a 502/503 and rolls back the in-flight transaction — spec §5.13
  does not allow a DB-only commit because that would silently create
  the drift-window-forever class of bug.
"""

from __future__ import annotations

import logging
from typing import Any

from forge.config import Settings

logger = logging.getLogger(__name__)


class CognitoSyncError(Exception):
    """Raised when Cognito fails to accept an attribute update or sign-out.

    The service layer catches this and converts to a 502 Bad Gateway.
    Why 502 (not 500): the API itself is healthy; an upstream (Cognito)
    is refusing.  This lets the operator page on upstream availability
    without being woken up for an application bug.
    """


def _cognito_enabled(settings: Settings) -> bool:
    """Whether live boto3 sync should run.

    We talk to real Cognito only when auth_mode is cognito AND a user
    pool id is configured.  Local / test environments where auth_mode
    is 'local' fall into a deterministic no-op path — the service
    layer still treats every call as successful so admin flows behave
    the same end-to-end, but no boto3 call is issued.
    """
    return settings.auth_mode == "cognito" and bool(
        settings.cognito_user_pool_id
    )


def _csv(values: list[str]) -> str:
    """Join a list of claim values into the CSV form Cognito stores.

    Cognito custom attributes are strings.  Our auth context already
    parses both CSV and JSON arrays (build_auth_context), so writing
    CSV keeps the round-trip trivially symmetrical.
    """
    return ",".join(sorted(set(values)))


def _get_client(settings: Settings) -> Any:
    """Create a boto3 cognito-idp client.

    Imported lazily so local / test paths do not require boto3 at
    runtime.  Fails loud if boto3 is missing in a live environment —
    that is a deployment misconfiguration, not a silent fallback.
    """
    import boto3

    return boto3.client("cognito-idp", region_name=settings.cognito_region)


def sync_user_attributes(
    *,
    settings: Settings,
    cognito_sub: str,
    roles: list[str],
    programs: list[str],
) -> None:
    """Push custom:roles and custom:programs to Cognito.

    Called immediately by admin endpoints after a role or program
    membership change (spec §5.13).  No-ops in local / test mode.
    Raises CognitoSyncError on any exception so the service layer
    can roll back the DB transaction.
    """
    if not _cognito_enabled(settings):
        logger.debug(
            "Cognito sync skipped (auth_mode=%s); sub=%s",
            settings.auth_mode, cognito_sub,
        )
        return

    try:
        client = _get_client(settings)
        client.admin_update_user_attributes(
            UserPoolId=settings.cognito_user_pool_id,
            Username=cognito_sub,
            UserAttributes=[
                {"Name": "custom:roles", "Value": _csv(roles)},
                {"Name": "custom:programs", "Value": _csv(programs)},
            ],
        )
    except Exception as exc:
        logger.exception(
            "Cognito AdminUpdateUserAttributes failed; sub=%s", cognito_sub,
        )
        raise CognitoSyncError(
            f"Failed to sync attributes for user {cognito_sub}"
        ) from exc


def global_sign_out(
    *, settings: Settings, cognito_sub: str,
) -> None:
    """Invalidate all refresh tokens for a user (spec §5.0.4).

    Called on deactivation and admin-role removal.  This bounds the
    exposure window to the remaining access-token TTL (≤30 min).
    No-ops in local / test mode.
    """
    if not _cognito_enabled(settings):
        logger.debug(
            "Global sign-out skipped (auth_mode=%s); sub=%s",
            settings.auth_mode, cognito_sub,
        )
        return

    try:
        client = _get_client(settings)
        client.admin_user_global_sign_out(
            UserPoolId=settings.cognito_user_pool_id,
            Username=cognito_sub,
        )
    except Exception as exc:
        logger.exception(
            "Cognito AdminUserGlobalSignOut failed; sub=%s", cognito_sub,
        )
        raise CognitoSyncError(
            f"Failed to global-sign-out user {cognito_sub}"
        ) from exc


def verify_pre_token_trigger_configured(settings: Settings) -> bool:
    """Return True if the Pre-Token-Generation Lambda ARN is configured.

    Spec §5.0.4: "Atlas must provision and verify the Cognito Pre Token
    Generation trigger before declaring auth complete."  This helper is
    a cheap configuration check used by the admin health endpoint (and
    acceptance tests) to confirm the ARN is wired up.  It does *not*
    introspect the user pool — that would require a second API call
    and a permission we do not need to carry in the tight path.  In
    local / test mode the ARN is intentionally empty; the helper
    returns True there so dev loops are not blocked.
    """
    if settings.auth_mode != "cognito":
        return True
    return bool(settings.cognito_pre_token_gen_lambda_arn)
