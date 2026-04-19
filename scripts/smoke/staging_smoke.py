#!/usr/bin/env python3
"""
Module:   Staging Smoke Harness
Purpose:  Post-deploy smoke probes for staging GovCloud environments.
          Runs a finite series of HTTP probes against the staging
          ALB, verifies the deployment contract, and exits non-zero
          on any failure so the GitHub Actions deploy job can gate
          the next stage.
Rev:      0.1.0

BoundState Proprietary
Co-Authored-By: Dr. Joseph Wilde, BoundState AI
Co-Authored-By: Claude "Beth" (Opus 4.7)

What this verifies:
  - Frontend root returns 200 + HTML (nginx SPA serving works).
  - API /health returns 200 + JSON (API is reachable via ALB).
  - ALB path routing splits correctly (/ -> frontend, /api/* -> API).
  - Security headers match spec §9.3.8 on the frontend response.
  - CORS allow-list behavior matches spec §9.3.9 (staging origin
    accepted; random origin rejected).  Preflight includes
    Access-Control-Request-Method so FastAPI's CORS middleware
    actually runs.
  - A protected manufacturing read path — `GET /api/work-orders` —
    returns 401 without auth AND returns 200 with a real token.
    The authenticated probe is MANDATORY for staging; if no token
    can be obtained (no test creds + no pre-minted token), the
    run fails rather than silently skip.

Authentication flow:
  Preferred: `STAGING_TEST_TOKEN` env supplies an already-minted
  access token.  The deploy workflow mints this token in a
  dedicated pre-smoke step via `aws cognito-idp admin-initiate-auth`
  and exports it — that is how every staging deploy reaches the
  smoke job.  See `.github/workflows/deploy-staging.yml`.

  Fallback: `STAGING_TEST_USERNAME` + `STAGING_TEST_PASSWORD` +
  `STAGING_COGNITO_USER_POOL_ID` + `STAGING_COGNITO_APP_CLIENT_ID`
  let the script exchange credentials via the Cognito
  `AdminInitiateAuth` flow (`ADMIN_USER_PASSWORD_AUTH`).  This
  flow is IAM-gated, so it only works when the caller's AWS
  credentials have `cognito-idp:AdminInitiateAuth` — the workflow's
  OIDC deploy role does, a random developer running the script
  locally typically does not.  The Cognito client is provisioned
  with `admin_user_password=True` to enable this flow; the
  end-user-facing `USER_PASSWORD_AUTH` flow stays DISABLED so
  browsers cannot bypass SRP.

  Either path is acceptable; missing both is a smoke failure.

Exit contract:
  Exit 0 iff every probe passes.  Exit 1 on any failure or
  unrecoverable auth-setup failure.  A summary table prints to
  stdout regardless of outcome.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One smoke probe's outcome.  Rendered into the summary table."""

    name: str
    ok: bool
    detail: str


# --- Required security headers (spec §9.3.8) --------------------------

_REQUIRED_HEADERS: tuple[tuple[str, str], ...] = (
    ("Strict-Transport-Security", "max-age=31536000"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    # Content-Security-Policy value is long; we just assert presence
    # + the connect-src fragment below rather than a byte-for-byte
    # match so a legitimate nginx reformat doesn't break smoke.
)


def _probe_frontend_root(client: httpx.Client, base_url: str) -> ProbeResult:
    r = client.get(f"{base_url}/")
    ok = (
        r.status_code == 200
        and "text/html" in r.headers.get("content-type", "").lower()
        and len(r.content) > 0
    )
    return ProbeResult(
        name="frontend_root",
        ok=ok,
        detail=(
            f"GET / -> {r.status_code} "
            f"({r.headers.get('content-type', '')})"
        ),
    )


def _probe_api_health(client: httpx.Client, base_url: str) -> ProbeResult:
    r = client.get(f"{base_url}/api/health")
    try:
        body = r.json()
    except Exception:
        body = None
    ok = (
        r.status_code == 200
        and isinstance(body, dict)
    )
    return ProbeResult(
        name="api_health",
        ok=ok,
        detail=(
            f"GET /api/health -> {r.status_code} "
            f"({'json' if isinstance(body, dict) else 'non-json'})"
        ),
    )


def _probe_routing_split(client: httpx.Client, base_url: str) -> ProbeResult:
    """Prove the ALB rule routes `/api/*` to API, default to frontend.

    We fetch both endpoints in one probe so a routing misconfig
    (e.g. catch-all forwarding everything to frontend, which would
    make `/api/health` return HTML) fires here rather than a
    confusing pass on `_probe_api_health`.
    """
    r_front = client.get(f"{base_url}/")
    r_api = client.get(f"{base_url}/api/health")
    front_html = "text/html" in r_front.headers.get("content-type", "").lower()
    api_json = "application/json" in r_api.headers.get(
        "content-type", "",
    ).lower()
    ok = front_html and api_json
    return ProbeResult(
        name="alb_routing_split",
        ok=ok,
        detail=(
            f"/ -> {r_front.headers.get('content-type', '?')}; "
            f"/api/health -> {r_api.headers.get('content-type', '?')}"
        ),
    )


def _probe_security_headers(client: httpx.Client, base_url: str) -> ProbeResult:
    r = client.get(f"{base_url}/")
    missing: list[str] = []
    for name, required_substring in _REQUIRED_HEADERS:
        val = r.headers.get(name)
        if val is None or required_substring not in val:
            missing.append(name)
    # CSP: verify present AND carries the `connect-src 'self'`
    # fragment so a future rewrite that accidentally drops the
    # connect-src allowance is caught.
    csp = r.headers.get("Content-Security-Policy", "")
    if "connect-src" not in csp or "'self'" not in csp:
        missing.append("Content-Security-Policy")
    ok = not missing
    return ProbeResult(
        name="security_headers",
        ok=ok,
        detail=("all present" if ok else f"missing: {', '.join(missing)}"),
    )


def _probe_cors_allowed_origin(
    client: httpx.Client, base_url: str, allowed_origin: str,
) -> ProbeResult:
    """Preflight from the configured staging origin must be accepted.

    Per the review note: include Access-Control-Request-Method so
    FastAPI's CORS middleware actually processes this as a
    preflight; without it, the OPTIONS lands on the generic
    handler and our Access-Control-Allow-Origin assertion would
    not be probing the real CORS path.
    """
    r = client.options(
        f"{base_url}/api/health",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": (
                "Authorization, X-Program-Id"
            ),
        },
    )
    allow = r.headers.get("Access-Control-Allow-Origin", "")
    ok = r.status_code in (200, 204) and allow == allowed_origin
    return ProbeResult(
        name="cors_allowed_origin",
        ok=ok,
        detail=(
            f"OPTIONS (Origin={allowed_origin}) -> {r.status_code} "
            f"Allow-Origin={allow!r}"
        ),
    )


def _probe_cors_rejected_origin(
    client: httpx.Client, base_url: str,
) -> ProbeResult:
    """Preflight from an unexpected origin must NOT echo the origin.

    Per spec §9.3.9: other origins rejected.  FastAPI's CORS
    middleware typically returns the request without a
    `Access-Control-Allow-Origin` header when the origin is not on
    the allowlist; that's what we assert here.
    """
    rogue_origin = "https://evil.example.com"
    r = client.options(
        f"{base_url}/api/health",
        headers={
            "Origin": rogue_origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    allow = r.headers.get("Access-Control-Allow-Origin")
    ok = allow != rogue_origin  # must not echo the rogue origin
    return ProbeResult(
        name="cors_rejected_origin",
        ok=ok,
        detail=(
            f"OPTIONS (Origin={rogue_origin}) -> {r.status_code} "
            f"Allow-Origin={allow!r}"
        ),
    )


def _probe_protected_no_auth(
    client: httpx.Client, base_url: str,
) -> ProbeResult:
    """Protected endpoint without Authorization header must 401."""
    r = client.get(
        f"{base_url}/api/work-orders",
        headers={"X-Program-Id": "*"},
    )
    ok = r.status_code == 401
    return ProbeResult(
        name="protected_unauth_rejects",
        ok=ok,
        detail=f"GET /api/work-orders (no auth) -> {r.status_code}",
    )


def _probe_protected_authenticated(
    client: httpx.Client,
    base_url: str,
    token: str,
) -> ProbeResult:
    """Authenticated manufacturing read path — mandatory on staging.

    `X-Program-Id: *` because spec §3.2 makes `*` the multi-program
    marker for list reads (pinned by
    tests/unit/test_program_scope.py).  Omitting the header would
    either 400 MISSING_PROGRAM_CONTEXT or land on a single-program
    endpoint that rejects `*` — neither is what we're probing.
    """
    r = client.get(
        f"{base_url}/api/work-orders",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Program-Id": "*",
        },
    )
    try:
        body = r.json()
    except Exception:
        body = None
    # Expect either an {items,total,page,page_size,pages} envelope
    # or a truthful-shape error envelope if the test user has no
    # program memberships.  A 200 with a paginated envelope is the
    # strongest proof; we accept 200 + envelope shape only.
    ok = (
        r.status_code == 200
        and isinstance(body, dict)
        and "items" in body
        and "total" in body
        and isinstance(body.get("items"), list)
    )
    return ProbeResult(
        name="protected_auth_reads_wos",
        ok=ok,
        detail=(
            f"GET /api/work-orders (auth) -> {r.status_code} "
            f"items={len(body.get('items', [])) if isinstance(body, dict) else '?'}"
        ),
    )


# --- Auth-token acquisition ------------------------------------------

def _obtain_token(env: dict[str, str]) -> str | None:
    """Return a valid access token or `None` if none is obtainable.

    Returning None is a smoke FAILURE — authenticated probes are
    mandatory.  We surface it as a ProbeResult with `ok=False`
    rather than raising so the summary table reports it alongside
    the other probes.
    """
    pre_minted = env.get("STAGING_TEST_TOKEN")
    if pre_minted:
        return pre_minted.strip()

    user = env.get("STAGING_TEST_USERNAME")
    password = env.get("STAGING_TEST_PASSWORD")
    pool_id = env.get("STAGING_COGNITO_USER_POOL_ID")
    client_id = env.get("STAGING_COGNITO_APP_CLIENT_ID")
    region = env.get("AWS_REGION") or env.get("STAGING_REGION") or "us-gov-west-1"

    if not all([user, password, pool_id, client_id]):
        return None

    # boto3 is a project dep; import lazily so the smoke module
    # stays importable on machines that skipped the [dev] extras.
    import boto3  # type: ignore[import-untyped]

    client = boto3.client("cognito-idp", region_name=region)
    try:
        # ADMIN_USER_PASSWORD_AUTH is used instead of
        # USER_PASSWORD_AUTH because the Cognito client leaves the
        # end-user USER_PASSWORD flow disabled (browsers would
        # otherwise bypass SRP).  The ADMIN variant is IAM-gated
        # — the caller's AWS credentials must have
        # `cognito-idp:AdminInitiateAuth`.
        resp = client.admin_initiate_auth(
            UserPoolId=pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": user,
                "PASSWORD": password,
            },
        )
    except Exception:
        return None
    auth = resp.get("AuthenticationResult") or {}
    token = auth.get("AccessToken")
    return str(token) if token else None


# --- Runner ----------------------------------------------------------

def run_probes(
    base_url: str,
    allowed_origin: str,
    token: str | None,
    *,
    client: httpx.Client | None = None,
) -> list[ProbeResult]:
    """Execute every probe in order.  Returns the full result list.

    `client` is injectable so `tests/unit/test_staging_smoke_contract.py`
    can pass a mock transport.  When None, a real httpx.Client is
    constructed.
    """
    close_client = False
    if client is None:
        client = httpx.Client(timeout=10.0, follow_redirects=False)
        close_client = True
    try:
        results: list[ProbeResult] = [
            _probe_frontend_root(client, base_url),
            _probe_api_health(client, base_url),
            _probe_routing_split(client, base_url),
            _probe_security_headers(client, base_url),
            _probe_cors_allowed_origin(client, base_url, allowed_origin),
            _probe_cors_rejected_origin(client, base_url),
            _probe_protected_no_auth(client, base_url),
        ]
        if token is None:
            results.append(ProbeResult(
                name="protected_auth_reads_wos",
                ok=False,
                detail=(
                    "no auth token available — set "
                    "STAGING_TEST_TOKEN or STAGING_TEST_USERNAME + "
                    "STAGING_TEST_PASSWORD + "
                    "STAGING_COGNITO_APP_CLIENT_ID"
                ),
            ))
        else:
            results.append(
                _probe_protected_authenticated(
                    client, base_url, token,
                ),
            )
    finally:
        if close_client:
            client.close()
    return results


def _render_summary(results: list[ProbeResult]) -> str:
    width = max(len(r.name) for r in results)
    lines = ["Probe results:"]
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        lines.append(f"  {status}  {r.name:<{width}}  {r.detail}")
    passed = sum(1 for r in results if r.ok)
    total = len(results)
    lines.append(f"  -> {passed}/{total} passed")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FORGE-MFG staging smoke harness",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("STAGING_BASE_URL"),
        help=(
            "ALB base URL (e.g. https://forge-mfg-staging.example.mil). "
            "Falls back to STAGING_BASE_URL env var."
        ),
    )
    parser.add_argument(
        "--allowed-origin",
        default=os.environ.get("STAGING_ALLOWED_ORIGIN"),
        help=(
            "CORS allowed origin to probe (e.g. "
            "https://forge-mfg-staging.example.mil).  Falls back to "
            "STAGING_ALLOWED_ORIGIN env; defaults to --base-url when "
            "unset."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.base_url:
        print(
            "ERROR: --base-url (or STAGING_BASE_URL) is required",
            file=sys.stderr,
        )
        return 2

    allowed_origin = args.allowed_origin or args.base_url
    token = _obtain_token(dict(os.environ))

    results = run_probes(args.base_url, allowed_origin, token)
    print(_render_summary(results))

    return 0 if all(r.ok for r in results) else 1


def _result_dict(results: list[ProbeResult]) -> dict[str, Any]:
    """Used by the unit-test harness to introspect the probe list."""
    return {r.name: {"ok": r.ok, "detail": r.detail} for r in results}


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    sys.exit(main())
