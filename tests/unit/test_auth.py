#!/usr/bin/env python3
"""
Module:   Test Auth
Purpose:  Prove Cognito auth middleware HTTP behavior with real RSA signatures.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Auth behavior tests must prove HTTP-level outcomes (200, 401, 403),
  not just helper-function decode results.  We generate a real RSA
  key pair, build JWTs signed by that key, and mock the JWKS fetch
  to return the matching public key.  This proves the full middleware
  chain: header extraction → JWKS lookup → signature verification →
  claim extraction → permission/program enforcement.

  A test-only probe route (/test/protected) is registered on the test
  app to exercise auth without shipping placeholder product endpoints.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from forge.api.app import create_app
from forge.api.auth import AuthContext, get_auth_context, require_permission
from forge.config import Settings

# ---------------------------------------------------------------------------
# RSA key fixtures — generate once per test module
# ---------------------------------------------------------------------------

_KID = "test-kid-001"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_PUBLIC_KEY = _PRIVATE_KEY.public_key()

# PEM for PyJWT test signing
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)

# JWK dict for JWKS mock
_PUBLIC_NUMBERS = _PUBLIC_KEY.public_numbers()


def _int_to_base64url(n: int, length: int) -> str:
    """Encode an integer as a base64url string (JWK format)."""
    import base64

    data = n.to_bytes(length, byteorder="big")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


_JWK = {
    "kty": "RSA",
    "kid": _KID,
    "use": "sig",
    "alg": "RS256",
    "n": _int_to_base64url(_PUBLIC_NUMBERS.n, 256),
    "e": _int_to_base64url(_PUBLIC_NUMBERS.e, 3),
}


# ---------------------------------------------------------------------------
# Token builder
# ---------------------------------------------------------------------------

_POOL_ID = "us-gov-west-1_TestPool"
_REGION = "us-gov-west-1"
_CLIENT_ID = "test-client-id"
_ISSUER = f"https://cognito-idp.{_REGION}.amazonaws.com/{_POOL_ID}"


def _build_token(
    *,
    token_use: str = "access",
    roles: str = "operator",
    programs: str = "RS25",
    sub: str | None = None,
    client_id: str = _CLIENT_ID,
    kid: str = _KID,
    exp_offset: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Build a signed JWT with configurable claims."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub or str(uuid.uuid4()),
        "iss": _ISSUER,
        "client_id": client_id,
        "token_use": token_use,
        "scope": "openid",
        "auth_time": now,
        "iat": now,
        "exp": now + exp_offset,
        "username": "testuser",
    }
    if roles is not None:
        claims["custom:roles"] = roles
    if programs is not None:
        claims["custom:programs"] = programs
    if extra_claims:
        claims.update(extra_claims)

    result: str = jwt.encode(
        claims,
        _PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return result


# ---------------------------------------------------------------------------
# Test app fixture — cognito mode with a probe route
# ---------------------------------------------------------------------------


@pytest.fixture()
def cognito_settings() -> Settings:
    """Settings that enable Cognito auth mode for middleware testing."""
    return Settings(
        env="test",
        debug=False,
        auth_mode="cognito",
        cognito_user_pool_id=_POOL_ID,
        cognito_region=_REGION,
        cognito_app_client_id=_CLIENT_ID,
    )


@pytest.fixture()
def auth_app(cognito_settings: Settings) -> FastAPI:
    """FastAPI app with a test-only protected probe route."""
    app = create_app(settings=cognito_settings)

    # Test-only probe route requiring wo:read permission.
    # Not a product endpoint — exists solely to prove middleware behavior.
    @app.get("/test/protected")
    async def probe(
        auth: AuthContext = Depends(require_permission("wo:read")),
    ) -> dict[str, Any]:
        return {
            "sub": auth.sub,
            "roles": auth.roles,
            "programs": auth.programs,
            "permissions": sorted(auth.permissions),
        }

    # Test-only route that checks program scope
    @app.get("/test/program/{code}")
    async def program_probe(
        code: str,
        auth: AuthContext = Depends(get_auth_context),
    ) -> dict[str, Any]:
        if not auth.has_program_access(code):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=403,
                detail=f"No access to program: {code}",
            )
        return {"program": code, "allowed": True}

    return app


@pytest.fixture()
async def auth_client(auth_app: FastAPI) -> AsyncGenerator[AsyncClient]:
    """Async HTTP client wired to the auth-enabled test app."""
    transport = ASGITransport(app=auth_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mock_jwks() -> Any:
    """Patch JWKS fetch to return our test public key."""
    mock = AsyncMock(return_value=[_JWK])
    return patch("forge.api.auth._fetch_jwks", mock)


# ---------------------------------------------------------------------------
# Behavior tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_token_correct_permission_200(
    auth_client: AsyncClient,
) -> None:
    """Valid access token + correct permission + correct program → 200."""
    token = _build_token(roles="operator", programs="RS25")
    with _mock_jwks():
        resp = await auth_client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["roles"] == ["operator"]
    assert body["programs"] == ["RS25"]
    assert "wo:read" in body["permissions"]


@pytest.mark.asyncio
async def test_missing_bearer_token_401(
    auth_client: AsyncClient,
) -> None:
    """Missing Bearer token on a protected route → 401."""
    resp = await auth_client.get("/test/protected")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_signature_401(
    auth_client: AsyncClient,
) -> None:
    """Token signed with a different key → 401."""
    # Generate a different key to produce an invalid signature
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_pem = wrong_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    claims = {
        "sub": str(uuid.uuid4()),
        "iss": _ISSUER,
        "client_id": _CLIENT_ID,
        "token_use": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "username": "badactor",
        "custom:roles": "operator",
        "custom:programs": "RS25",
    }
    bad_token = jwt.encode(
        claims, wrong_pem, algorithm="RS256", headers={"kid": _KID}
    )
    with _mock_jwks():
        resp = await auth_client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {bad_token}"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_id_token_rejected_401(
    auth_client: AsyncClient,
) -> None:
    """ID token presented as Bearer → 401.

    Spec §5.0.4: 'ID tokens are not accepted by the API.'
    Cognito ID tokens have token_use='id'.
    """
    token = _build_token(token_use="id")
    with _mock_jwks():
        resp = await auth_client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 401
    assert "ID tokens" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_missing_permission_403(
    auth_client: AsyncClient,
) -> None:
    """Valid token but missing required permission → 403.

    The probe route requires wo:read.  Every spec §5.0.2 role
    includes wo:read, so we use an empty roles claim to produce
    a user with zero permissions.
    """
    # Empty roles → empty permissions → missing wo:read
    token = _build_token(roles="", programs="RS25")
    with _mock_jwks():
        resp = await auth_client.get(
            "/test/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    assert "permission" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_wrong_program_scope_403(
    auth_client: AsyncClient,
) -> None:
    """Valid token but wrong program scope → 403."""
    token = _build_token(roles="operator", programs="SLS-ADAPTER")
    with _mock_jwks():
        resp = await auth_client.get(
            "/test/program/RS25",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_multi_program_claim(
    auth_client: AsyncClient,
) -> None:
    """Multi-program claim works — user belongs to both programs.

    Spec §5.0.3: program scope is driven by custom:programs, which
    can contain multiple programs.  Do not reduce to a single program_id.
    """
    token = _build_token(roles="operator", programs="RS25,SLS-ADAPTER")
    with _mock_jwks():
        # Both programs should be accessible
        resp1 = await auth_client.get(
            "/test/program/RS25",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp2 = await auth_client.get(
            "/test/program/SLS-ADAPTER",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp1.status_code == 200
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_health_no_auth(auth_client: AsyncClient) -> None:
    """/health and /api/health must return 200 without any auth."""
    resp1 = await auth_client.get("/health")
    resp2 = await auth_client.get("/api/health")
    assert resp1.status_code == 200
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_options_preflight_bypass(
    auth_client: AsyncClient,
) -> None:
    """OPTIONS preflight must bypass auth (CORS contract)."""
    resp = await auth_client.options("/test/protected")
    # Should not be 401 — OPTIONS is not authenticated
    assert resp.status_code != 401


# ---------------------------------------------------------------------------
# JWKS rotation tests — exercise real cache/refetch behavior
# ---------------------------------------------------------------------------


def _make_jwk_from_key(
    private_key: rsa.RSAPrivateKey, kid: str
) -> dict[str, str]:
    """Build a JWK dict from an RSA private key for JWKS mocking."""
    pub = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_base64url(pub.n, 256),
        "e": _int_to_base64url(pub.e, 3),
    }


@pytest.mark.asyncio
async def test_jwks_rotation_kid_miss_triggers_refresh() -> None:
    """Cached JWKS with old kid + token signed by new kid → refresh → success.

    Tests _resolve_signing_key directly to exercise the real cache/refetch
    control flow.  Proves: when a token's kid is not in the cached JWKS,
    the system force-refreshes once.  If the refreshed JWKS contains the
    new kid, key resolution succeeds.
    """
    from forge.api.auth import _jwks_cache, _resolve_signing_key

    # Generate a "new" key pair (simulating Cognito key rotation)
    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new_kid = "rotated-kid-002"
    new_pem = new_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    new_jwk = _make_jwk_from_key(new_key, new_kid)

    # Build a token signed by the new key
    now = int(time.time())
    claims_dict = {
        "sub": str(uuid.uuid4()),
        "iss": _ISSUER,
        "client_id": _CLIENT_ID,
        "token_use": "access",
        "iat": now,
        "exp": now + 3600,
        "username": "rotated_user",
        "custom:roles": "operator",
        "custom:programs": "RS25",
    }
    token = jwt.encode(
        claims_dict, new_pem, algorithm="RS256", headers={"kid": new_kid}
    )

    jwks_url = f"{_ISSUER}/.well-known/jwks.json"

    # Pre-populate cache with OLD keys only (does not contain new_kid)
    _jwks_cache.clear()
    _jwks_cache[jwks_url] = [_JWK]

    # Mock the network fetch to return rotated keyset (old + new)
    fetch_count = 0

    async def mock_fetch(
        url: str, *, force_refresh: bool = False
    ) -> list[dict[str, Any]]:
        nonlocal fetch_count
        if not force_refresh:
            # Return cached (old-only) keys on non-forced calls
            cached = _jwks_cache.get(url)
            if cached is not None:
                return cached
        # On force_refresh (or cold miss), return rotated keyset
        fetch_count += 1
        rotated = [_JWK, new_jwk]
        _jwks_cache[url] = rotated
        return rotated

    with patch("forge.api.auth._fetch_jwks", mock_fetch):
        resolved_key = await _resolve_signing_key(token, jwks_url)

    assert resolved_key["kid"] == new_kid
    # Exactly one forced refresh should have occurred
    assert fetch_count == 1, f"Expected 1 forced fetch, got {fetch_count}"

    _jwks_cache.clear()


@pytest.mark.asyncio
async def test_jwks_rotation_unknown_kid_fails_after_refresh() -> None:
    """Cached JWKS with old kid + token with unknown kid → refresh → fail.

    Tests _resolve_signing_key directly.  Proves: when a token's kid is
    not in the cached JWKS, the system force-refreshes once.  If the
    refreshed JWKS still lacks the kid, a PyJWTError is raised — no
    infinite retry loop.
    """
    from jwt import PyJWTError as JoseJWTError

    from forge.api.auth import _jwks_cache, _resolve_signing_key

    # Generate a completely unknown key (not in any JWKS response)
    unknown_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    unknown_kid = "totally-unknown-kid"
    unknown_pem = unknown_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    now = int(time.time())
    claims_dict = {
        "sub": str(uuid.uuid4()),
        "iss": _ISSUER,
        "client_id": _CLIENT_ID,
        "token_use": "access",
        "iat": now,
        "exp": now + 3600,
        "username": "unknown_signer",
        "custom:roles": "operator",
        "custom:programs": "RS25",
    }
    token = jwt.encode(
        claims_dict, unknown_pem, algorithm="RS256",
        headers={"kid": unknown_kid},
    )

    jwks_url = f"{_ISSUER}/.well-known/jwks.json"

    # Pre-populate cache with old keys only
    _jwks_cache.clear()
    _jwks_cache[jwks_url] = [_JWK]

    fetch_count = 0

    async def mock_fetch(
        url: str, *, force_refresh: bool = False
    ) -> list[dict[str, Any]]:
        nonlocal fetch_count
        if not force_refresh:
            cached = _jwks_cache.get(url)
            if cached is not None:
                return cached
        # Refreshed keyset still only has the old key
        fetch_count += 1
        return [_JWK]

    with (
        patch("forge.api.auth._fetch_jwks", mock_fetch),
        pytest.raises(JoseJWTError, match="No matching JWKS key"),
    ):
        await _resolve_signing_key(token, jwks_url)

    # Exactly one forced refresh should have been attempted
    assert fetch_count == 1, f"Expected 1 forced fetch, got {fetch_count}"

    _jwks_cache.clear()
