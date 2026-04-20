#!/usr/bin/env python3
"""
Module:   Test Deploy Smoke Harness Contract
Purpose:  Verify the probe logic in `scripts.smoke.deploy_smoke`
          against mocked httpx responses.  These are NOT live
          tests — they pin the assertion logic so a bad header
          match or a silently-accepted rogue-origin preflight
          fails locally before the deploy workflow runs.

          Module file is named `test_staging_smoke_contract.py` for
          historical reasons (6A-2 era); the module under test is
          now `scripts.smoke.deploy_smoke` after the 6A-3B rename
          merged the staging + production smoke paths.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False
"""

from __future__ import annotations

import httpx

from scripts.smoke.deploy_smoke import ProbeResult, run_probes

# ── Mock transport helpers ─────────────────────────────────────────

# Canonical security headers expected on the frontend root response.
# A probe-contract failure here means the smoke harness would let a
# deploy through that is missing a required header.
_GOOD_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "strict-transport-security": (
        "max-age=31536000; includeSubDomains; preload"
    ),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "content-security-policy": (
        "default-src 'self'; connect-src 'self' "
        "https://cognito-idp.us-gov-west-1.amazonaws.com"
    ),
}


def _build_transport(
    frontend_body: bytes = b"<!doctype html><html></html>",
    api_health_body: bytes = b'{"status": "healthy"}',
    security_headers: dict[str, str] | None = None,
    cors_allowed_origin: str | None = (
        "https://forge-mfg-staging.example.mil"
    ),
    protected_unauth_status: int = 401,
    protected_auth_body: bytes | None = (
        b'{"items": [], "total": 0, "page": 1, '
        b'"page_size": 50, "pages": 0}'
    ),
    protected_auth_status: int = 200,
) -> httpx.MockTransport:
    """Construct an httpx MockTransport that answers every probe
    the harness fires.  Defaults represent a fully compliant
    staging deploy; individual tests override fields to simulate
    a specific regression.
    """
    sec = security_headers if security_headers is not None else _GOOD_HEADERS

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and path == "/":
            return httpx.Response(
                200, content=frontend_body,
                headers=dict(sec),
            )
        if method == "GET" and path == "/api/health":
            return httpx.Response(
                200, content=api_health_body,
                headers={
                    "content-type": "application/json",
                },
            )
        if method == "OPTIONS" and path == "/api/health":
            origin = request.headers.get("origin", "")
            # Mirror FastAPI CORS middleware behavior: echo allowed
            # origin on the allow-origin header; omit the header
            # entirely for rejected origins.
            headers: dict[str, str] = {
                "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "access-control-allow-headers": "Authorization, X-Program-Id",
            }
            if origin == cors_allowed_origin:
                headers["access-control-allow-origin"] = origin
                return httpx.Response(
                    204, headers=headers,
                )
            # Unknown origin — no allow-origin header emitted.
            return httpx.Response(400, headers=headers)
        if method == "GET" and path == "/api/work-orders":
            if "authorization" in {k.lower() for k in request.headers}:
                return httpx.Response(
                    protected_auth_status,
                    content=protected_auth_body or b"{}",
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(
                protected_unauth_status,
                content=b'{"detail": "not authorized"}',
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _run(
    transport: httpx.MockTransport,
    *,
    token: str | None = "test-access-token",
    base_url: str = "https://forge-mfg-staging.example.mil",
    allowed_origin: str | None = None,
) -> list[ProbeResult]:
    allowed = allowed_origin or base_url
    with httpx.Client(transport=transport, base_url="http://_", timeout=5.0) as c:
        # `base_url` on the client is irrelevant when probes issue
        # absolute URLs; run_probes passes the absolute base_url
        # into each probe for path construction.
        return run_probes(base_url, allowed, token, client=c)


# ── Happy path ────────────────────────────────────────────────────

def test_all_probes_pass_on_compliant_deploy() -> None:
    results = _run(_build_transport())
    failed = [r for r in results if not r.ok]
    assert not failed, (
        "compliant deploy must pass every probe — failed: "
        f"{[r.name + ': ' + r.detail for r in failed]}"
    )


# ── Security-header regressions ───────────────────────────────────

def test_missing_hsts_header_fails_probe() -> None:
    headers = {**_GOOD_HEADERS}
    del headers["strict-transport-security"]
    results = _run(_build_transport(security_headers=headers))
    sec_results = [r for r in results if r.name == "security_headers"]
    assert sec_results and not sec_results[0].ok
    assert "Strict-Transport-Security" in sec_results[0].detail


def test_missing_csp_header_fails_probe() -> None:
    headers = {**_GOOD_HEADERS}
    del headers["content-security-policy"]
    results = _run(_build_transport(security_headers=headers))
    sec_results = [r for r in results if r.name == "security_headers"]
    assert sec_results and not sec_results[0].ok
    assert "Content-Security-Policy" in sec_results[0].detail


def test_csp_without_connect_src_fails_probe() -> None:
    """A CSP header that lands in the response but is missing the
    `connect-src 'self'` fragment is still a smoke failure — spec
    §9.3.8 requires connect-src for Cognito + S3."""
    headers = {**_GOOD_HEADERS}
    headers["content-security-policy"] = "default-src 'self'"  # no connect-src
    results = _run(_build_transport(security_headers=headers))
    sec_results = [r for r in results if r.name == "security_headers"]
    assert sec_results and not sec_results[0].ok


# ── CORS behavior ────────────────────────────────────────────────

def test_rogue_origin_echoed_back_fails_probe() -> None:
    """If the backend mistakenly echoes an evil origin on preflight,
    the rejected-origin probe must fail."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "OPTIONS":
            # Misconfigured CORS: echoes ANY origin.
            return httpx.Response(
                204,
                headers={
                    "access-control-allow-origin": request.headers.get(
                        "origin", "",
                    ),
                },
            )
        return httpx.Response(200, content=b"<html></html>", headers=_GOOD_HEADERS)

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    ) as c:
        results = run_probes(
            "https://forge-mfg-staging.example.mil",
            "https://forge-mfg-staging.example.mil",
            "tok",
            client=c,
        )
    rejected = [r for r in results if r.name == "cors_rejected_origin"]
    assert rejected and not rejected[0].ok


# ── Protected path ────────────────────────────────────────────────

def test_missing_token_fails_auth_probe() -> None:
    """`STAGING_TEST_TOKEN` + creds both absent → authenticated
    probe reports failure (per 6A-2 review adjustment 4, the auth
    probe is mandatory on staging)."""
    results = _run(_build_transport(), token=None)
    auth_results = [r for r in results if r.name == "protected_auth_reads_wos"]
    assert auth_results and not auth_results[0].ok
    assert "no auth token" in auth_results[0].detail


def test_auth_probe_requires_paginated_envelope() -> None:
    """A 200 that doesn't look like a paginated envelope is a bug.
    The probe must not silently pass on malformed responses."""
    results = _run(_build_transport(
        protected_auth_body=b'"ok"',  # not a dict
    ))
    auth_results = [r for r in results if r.name == "protected_auth_reads_wos"]
    assert auth_results and not auth_results[0].ok


def test_unauthenticated_probe_requires_401() -> None:
    """A protected endpoint that returns 200 without auth is a
    critical regression — the unauth probe must fail in that case."""
    results = _run(_build_transport(protected_unauth_status=200))
    unauth = [r for r in results if r.name == "protected_unauth_rejects"]
    assert unauth and not unauth[0].ok


# ── Routing split ────────────────────────────────────────────────

def test_routing_split_fails_when_api_returns_html() -> None:
    """A misconfigured ALB rule that forwards `/api/*` to the
    frontend container would make `/api/health` return HTML —
    the routing probe must catch that."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            # Oops: ALB sent this to the frontend container.
            return httpx.Response(
                200, content=b"<html></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200, content=b"<html></html>",
            headers=_GOOD_HEADERS,
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    ) as c:
        results = run_probes(
            "https://forge-mfg-staging.example.mil",
            "https://forge-mfg-staging.example.mil",
            "tok",
            client=c,
        )
    split = [r for r in results if r.name == "alb_routing_split"]
    assert split and not split[0].ok


# ── Preflight shape (the review minor note) ──────────────────────

def test_obtain_token_uses_admin_initiate_auth() -> None:
    """Finding 3 reflection: the Cognito client disables
    USER_PASSWORD_AUTH so browsers cannot bypass SRP.  The smoke
    script must therefore use ADMIN_USER_PASSWORD_AUTH via
    `admin_initiate_auth` — IAM-gated, only usable by the deploy
    workflow's OIDC role.  If someone regresses the script back to
    `initiate_auth`, this test catches it before a failed staging
    smoke does."""
    import sys
    from types import ModuleType
    from unittest.mock import MagicMock

    from scripts.smoke import deploy_smoke as staging_smoke

    captured: dict[str, object] = {}

    fake_client = MagicMock()
    fake_client.admin_initiate_auth.return_value = {
        "AuthenticationResult": {"AccessToken": "real-token-xyz"},
    }

    def _fake_client_factory(
        service: str, region_name: str | None = None,
    ) -> MagicMock:
        captured["service"] = service
        captured["region"] = region_name
        return fake_client

    # Stub boto3 with a module-level shim so the lazy import inside
    # _obtain_token picks it up.
    fake_boto3 = ModuleType("boto3")
    fake_boto3.client = _fake_client_factory  # type: ignore[attr-defined]
    sys.modules["boto3"] = fake_boto3
    try:
        token = staging_smoke._obtain_token({
            "STAGING_TEST_USERNAME": "u@example.mil",
            "STAGING_TEST_PASSWORD": "pw",
            "STAGING_COGNITO_USER_POOL_ID": "us-gov-west-1_ABC",
            "STAGING_COGNITO_APP_CLIENT_ID": "client-xyz",
        })
    finally:
        sys.modules.pop("boto3", None)

    assert token == "real-token-xyz"
    assert captured["service"] == "cognito-idp"
    fake_client.admin_initiate_auth.assert_called_once()
    call_kwargs = fake_client.admin_initiate_auth.call_args.kwargs
    assert call_kwargs["AuthFlow"] == "ADMIN_USER_PASSWORD_AUTH", (
        f"smoke token mint must use ADMIN_USER_PASSWORD_AUTH "
        f"(finding 3); got {call_kwargs['AuthFlow']}"
    )
    assert call_kwargs["UserPoolId"] == "us-gov-west-1_ABC"
    assert call_kwargs["ClientId"] == "client-xyz"
    fake_client.initiate_auth.assert_not_called()


def test_obtain_token_prefers_pre_minted_env_var() -> None:
    """Finding 4: when the workflow has minted STAGING_TEST_TOKEN
    in a pre-smoke step (the default path), _obtain_token must not
    still fire admin_initiate_auth."""
    from scripts.smoke import deploy_smoke as staging_smoke

    token = staging_smoke._obtain_token({
        "STAGING_TEST_TOKEN": "pre-minted-abc",
        # Intentionally set username/password too — they should be
        # ignored when the pre-minted token is present.
        "STAGING_TEST_USERNAME": "u@example.mil",
        "STAGING_TEST_PASSWORD": "pw",
        "STAGING_COGNITO_USER_POOL_ID": "us-gov-west-1_ABC",
        "STAGING_COGNITO_APP_CLIENT_ID": "client-xyz",
    })
    assert token == "pre-minted-abc"


def test_obtain_token_returns_none_when_nothing_supplied() -> None:
    """No token, no credentials → None.  The runner surfaces None as
    a probe failure (see test_missing_token_fails_auth_probe)."""
    from scripts.smoke import deploy_smoke as staging_smoke

    assert staging_smoke._obtain_token({}) is None


def test_preflight_requests_include_access_control_request_method() -> None:
    """Per the review note: the CORS probe must send
    `Access-Control-Request-Method` so FastAPI's CORS middleware
    actually processes this as a preflight.  We assert the request
    header by capturing it inside the mock handler."""
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(dict(request.headers))
        if request.method == "OPTIONS":
            return httpx.Response(
                204,
                headers={
                    "access-control-allow-origin": request.headers.get(
                        "origin", "",
                    ),
                },
            )
        return httpx.Response(
            200, content=b"<html></html>",
            headers=_GOOD_HEADERS,
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    ) as c:
        run_probes(
            "https://forge-mfg-staging.example.mil",
            "https://forge-mfg-staging.example.mil",
            "tok",
            client=c,
        )

    preflights = [
        h for h in captured
        if h.get("access-control-request-method")
    ]
    assert preflights, (
        "no preflight carried Access-Control-Request-Method — "
        "FastAPI CORS middleware would not fire"
    )
    assert any(
        h.get("access-control-request-method") == "GET"
        for h in preflights
    )
