#!/usr/bin/env python3
"""
Module:   Auth
Purpose:  Cognito access-token verification, claim extraction, and
          permission/program-scope enforcement.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Cognito authenticates; the application database authorizes (spec §5.0.4).
  Access tokens carry custom:roles and custom:programs claims injected by
  a Cognito Pre Token Generation trigger.  Per-request authorization uses
  these JWT claims as the authority — the database is only queried for
  zero-drift actions (admin:manage, deactivation).

  ID tokens are explicitly rejected: the spec §5.0.4 states "ID tokens
  are not accepted by the API."

  JWKS keys are cached with a 1-hour TTL to avoid per-request network
  calls.  On key rotation, a cache miss triggers a fresh fetch.

  Claim sync from admin writes: admin endpoints that change a user's
  role or program set call Cognito AdminUpdateUserAttributes
  immediately, and deactivation or admin-role removal additionally
  calls AdminUserGlobalSignOut (spec §5.0.4).  Updated claims take
  effect on the next token refresh; the 30-minute drift window for
  normal actions is a deliberate tradeoff (spec §5.0.4).  See
  forge.services.cognito_sync.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt

from forge.config import Settings

logger = logging.getLogger(__name__)

# JWKS key cache: 1-hour TTL avoids per-request network calls.
# On Cognito key rotation, the cache will miss and fetch fresh keys.
_jwks_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(maxsize=2, ttl=3600)


# ---------------------------------------------------------------------------
# AuthContext — structured auth state attached to request.state.auth
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Authenticated user context extracted from a Cognito access token.

    Attached to request.state.auth by the auth middleware so downstream
    route handlers and dependencies can check permissions and program
    scope without re-parsing the token.

    Roles and programs are lists (not single values) because users may
    hold multiple roles and belong to multiple programs (spec §5.0.2,
    §5.0.3).  Effective permissions are the union of all assigned roles'
    permissions — computed at the claim source (Cognito trigger) and
    carried as custom:roles, not re-derived here.
    """

    sub: str
    username: str
    roles: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has_permission(self, permission: str) -> bool:
        """Check if this user has the given permission string."""
        return permission in self.permissions

    def has_program_access(self, program_code: str) -> bool:
        """Check if this user belongs to the given program.

        The wildcard "*" grants access to all programs (local dev only).
        """
        return "*" in self.programs or program_code in self.programs


# ---------------------------------------------------------------------------
# JWKS retrieval and token verification
# ---------------------------------------------------------------------------


async def _fetch_jwks(
    jwks_url: str, *, force_refresh: bool = False
) -> list[dict[str, Any]]:
    """Fetch JWKS keys from the Cognito well-known endpoint.

    Cached for 1 hour.  When force_refresh=True, the cached entry is
    evicted before fetching — used on kid-miss to handle Cognito key
    rotation without waiting for TTL expiry.
    """
    if force_refresh:
        _jwks_cache.pop(jwks_url, None)
    else:
        cached = _jwks_cache.get(jwks_url)
        if cached is not None:
            return cached

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        keys: list[dict[str, Any]] = resp.json().get("keys", [])
    if not keys:
        raise JWTError("JWKS key set is empty")
    _jwks_cache[jwks_url] = keys
    return keys


def _get_token_kid(token: str) -> str:
    """Extract the kid from a JWT header.

    Every Cognito token includes the kid of the signing key.
    Returns the kid string or raises JWTError if absent.
    """
    headers = jwt.get_unverified_headers(token)
    kid: str | None = headers.get("kid")
    if not kid:
        raise JWTError("JWT missing kid header")
    return kid


def _find_key_by_kid(
    kid: str, keys: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Find the JWKS key matching a kid, or None if not found.

    Why kid matching: Cognito rotates signing keys periodically.
    Each token's header contains the kid of the key that signed it.
    We must find the matching public key to verify the signature.
    """
    for key in keys:
        if key.get("kid") == kid:
            return key
    return None


async def _resolve_signing_key(
    token: str, jwks_url: str
) -> dict[str, Any]:
    """Resolve the signing key for a token, handling key rotation.

    Strategy:
    1. Fetch JWKS from cache (or network on cold start).
    2. Look up the token's kid in the cached keys.
    3. If kid is not found, force-refresh the JWKS once — Cognito
       may have rotated keys since the last cache fill.
    4. Retry the kid lookup with refreshed keys.
    5. If still not found, the token's kid is genuinely unknown.

    This avoids infinite retry loops: at most one forced refresh per
    token verification attempt.
    """
    kid = _get_token_kid(token)

    # First attempt: use cached JWKS
    keys = await _fetch_jwks(jwks_url)
    key = _find_key_by_kid(kid, keys)
    if key is not None:
        return key

    # Kid not in cache — force a single refresh for key rotation
    logger.info("JWKS kid miss (kid=%s), forcing refresh", kid)
    keys = await _fetch_jwks(jwks_url, force_refresh=True)
    key = _find_key_by_kid(kid, keys)
    if key is not None:
        return key

    raise JWTError(f"No matching JWKS key for kid={kid}")


async def verify_access_token(
    token: str, settings: Settings
) -> dict[str, Any]:
    """Verify a Cognito access token and return its claims.

    Enforces:
    - RS256 signature against JWKS public key (with rotation handling)
    - Issuer matches the configured Cognito user pool
    - token_use == "access" (ID tokens are explicitly rejected, spec §5.0.4)
    - client_id matches the configured Cognito app client
    """
    issuer = (
        f"https://cognito-idp.{settings.cognito_region}"
        f".amazonaws.com/{settings.cognito_user_pool_id}"
    )
    jwks_url = f"{issuer}/.well-known/jwks.json"

    key = await _resolve_signing_key(token, jwks_url)

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False, "verify_at_hash": False},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid or expired token"
        ) from exc

    # Spec §5.0.4: "ID tokens are not accepted by the API."
    # Cognito access tokens have token_use="access".
    # Cognito ID tokens have token_use="id".
    token_use = claims.get("token_use")
    if token_use != "access":
        raise HTTPException(
            status_code=401,
            detail="Access token required — ID tokens are not accepted",
        )

    # Verify the token was issued for our app client
    client_id = claims.get("client_id")
    if client_id != settings.cognito_app_client_id:
        raise HTTPException(status_code=401, detail="Token client_id mismatch")

    return claims


# ---------------------------------------------------------------------------
# Role-to-permission resolution
# ---------------------------------------------------------------------------

# Spec §5.0.2: complete role-to-permission mapping.
# This is the canonical source for per-request authorization.
# The same mapping is seeded in the roles table (migration 387cadd44f9d).
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "operator": frozenset({
        "op:execute", "labor:create", "labor:update_own",
        # UX spec v1.5.1 §5.4.9: operator-only actions on OTHER
        # users' labor entries.  These are NOT the same as
        # labor:update_own (which is the self-edit lane) — they
        # initiate an escalation to the engineer.
        "labor:request_stop", "labor:flag_correction",
        "mat:consume",
        "part:create", "part:update", "wo:read", "op:read", "ncr:read",
        "part:read", "doc:read", "sched:read", "dash:read", "chat:query",
    }),
    "inspector": frozenset({
        "inspect:create", "inspect:sign_off", "inspect:read",
        "mat:receive", "mat:accept",
        "ncr:create", "doc:read", "doc:upload", "wo:read", "op:read",
        "ncr:read", "part:read", "sched:read", "dash:read", "chat:query",
    }),
    "engineer": frozenset({
        "wo:create", "wo:update", "wo:release", "wo:recall",
        "wo:graph_edit",
        "wo:hold", "wo:resume",
        # UX spec v1.5.1 §5.4.9 Force-Stop API: engineer-only
        # authority to stop another user's active labor entry.
        # qa_manager deliberately does NOT get this even though
        # they hold labor:update_any — §5.4.9 LABOR-STOP-003
        # "Caller does not have engineer role" is explicit.
        "labor:force_stop",
        "wo:complete", "op:create", "op:update", "op:delete", "op:hold",
        "op:rework", "ncr:create", "ncr:disposition", "sched:create",
        "sched:baseline", "sched:what_if", "labor:update_any", "doc:create",
        "doc:read", "doc:upload", "doc:finalize", "wo:read", "op:read",
        "ncr:read", "part:read", "sched:read", "dash:read", "chat:query",
    }),
    "qa_manager": frozenset({
        "wo:create", "wo:update", "wo:release", "wo:hold", "wo:resume",
        "wo:complete", "wo:close", "op:create", "op:update", "op:delete",
        "op:hold", "op:rework", "ncr:create", "ncr:disposition", "ncr:close",
        "sched:create", "sched:baseline", "sched:what_if", "labor:update_any",
        "doc:create", "doc:read", "doc:upload", "doc:finalize", "wo:read",
        "op:read", "ncr:read", "part:read", "sched:read", "dash:read",
        # inspect:read: drill-through read-only per UX spec v1.5.1 §3.1
        # footnote 4.  qa_manager cannot record inspections (no
        # inspect:create) but must see the queue to resolve Dashboard
        # tiles.  Added alongside the §5.5.5 queue surface.
        "inspect:read",
        "chat:query",
    }),
    "program_manager": frozenset({
        "wo:read", "op:read", "ncr:read", "part:read", "doc:read",
        "sched:read", "dash:read", "chat:query",
    }),
    "admin": frozenset({
        "admin:manage", "wo:read", "op:read", "ncr:read", "part:read",
        "doc:read", "sched:read", "dash:read",
    }),
}


def _resolve_permissions(roles: list[str]) -> frozenset[str]:
    """Compute effective permissions as the union of all assigned roles.

    Spec §5.0.2: "Effective permission set is the union of all assigned
    roles' permissions."  Unknown role names are silently skipped — this
    is defense against stale claims after a role rename, not a feature.
    """
    perms: set[str] = set()
    for role in roles:
        role_perms = ROLE_PERMISSIONS.get(role)
        if role_perms:
            perms |= role_perms
    return frozenset(perms)


def _parse_csv_claim(value: Any) -> list[str]:
    """Parse a Cognito custom claim that may be a CSV string or list.

    Cognito custom attributes are strings, so custom:roles might arrive
    as "operator,inspector" rather than a JSON list.  Handle both forms.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def build_auth_context(claims: dict[str, Any]) -> AuthContext:
    """Build an AuthContext from verified Cognito access-token claims.

    Extracts custom:roles and custom:programs, resolves the effective
    permission set, and returns a frozen AuthContext.
    """
    roles = _parse_csv_claim(claims.get("custom:roles"))
    programs = _parse_csv_claim(claims.get("custom:programs"))
    permissions = _resolve_permissions(roles)

    return AuthContext(
        sub=str(claims.get("sub", "")),
        username=str(
            claims.get("username", claims.get("cognito:username", ""))
        ),
        roles=roles,
        programs=programs,
        permissions=permissions,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies for route-level enforcement
# ---------------------------------------------------------------------------


def get_auth_context(request: Request) -> AuthContext:
    """Retrieve the AuthContext set by the auth middleware.

    Returns 401 if no auth context is present (middleware did not
    authenticate the request).
    """
    ctx: AuthContext | None = getattr(request.state, "auth", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return ctx


def require_permission(permission: str) -> Any:
    """Factory that returns a FastAPI dependency enforcing a permission.

    Usage in a route:
        @router.get("/api/work-orders", dependencies=[Depends(require_permission("wo:read"))])

    Why a factory: each route declares exactly which spec §5.0.1 permission
    it requires, keeping authorization declarative and auditable.
    """

    def _check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_permission(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission: {permission}",
            )
        return auth

    return _check


def require_program_access(program_code: str) -> Any:
    """Factory that returns a FastAPI dependency enforcing program scope.

    Usage in a route where the program code is a path/query parameter:
        auth = require_program_access(program_code)(auth_ctx)

    Why: spec §5.0.3 requires program-scope filtering on all data-access
    endpoints.  This is typically enforced at the service layer, but
    route-level guards provide defense-in-depth.
    """

    def _check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_program_access(program_code):
            raise HTTPException(
                status_code=403,
                detail=f"No access to program: {program_code}",
            )
        return auth

    return _check
