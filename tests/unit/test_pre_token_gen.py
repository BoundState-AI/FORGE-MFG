#!/usr/bin/env python3
"""
Module:   Test PreTokenGeneration Lambda Handler
Purpose:  Pin the Cognito Pre Token Generation V2 handler's claim-
          injection contract (spec §5.0.4 line 907).  The API reads
          `custom:roles` + `custom:programs` off the access token
          for Bearer-auth authorization; any drift here silently
          breaks every permission check.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this is safety-critical:
  This handler is the sole authority translating the application
  DB's role/program state (synced into Cognito user attributes by
  the admin service) into the issued token claims the API
  authorizes on.  A regression here lets a user authenticate with
  no claims and silently fail open or silently fail closed in ways
  the acceptance tests may not catch.  Each test below pins one
  invariant.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Load the lambda handler as a standalone module — it has zero
# internal deps so a direct path-based import keeps the test
# isolated from CDK / aws-cdk-lib.  Import is performed lazily
# inside `_load_handler()` so the module-level import block stays
# ruff-clean.
_HANDLER_DIR = (
    Path(__file__).parents[2]
    / "infra" / "govcloud" / "assets" / "pre_token_gen"
)


def _load_handler() -> Any:
    sys.path.insert(0, str(_HANDLER_DIR))
    try:
        import handler as pre_token_gen  # type: ignore[import-not-found]
        return pre_token_gen
    finally:
        sys.path.remove(str(_HANDLER_DIR))


pre_token_gen = _load_handler()


def _event(
    attrs: dict[str, str] | None = None,
    *,
    username: str = "u-acme",
    trigger_source: str = "TokenGeneration_Authentication",
) -> dict[str, Any]:
    """Build a Cognito PreTokenGen V2 event stub."""
    return {
        "version": "2",
        "triggerSource": trigger_source,
        "userName": username,
        "request": {
            "userAttributes": attrs or {},
        },
        "response": {},
    }


def test_roles_and_programs_copied_to_both_tokens() -> None:
    """Given current user attrs, the handler emits identical claim
    overrides onto the access token AND the id token.  V2 trigger
    response shape must carry `claimsAndScopeOverrideDetails`."""
    event = _event({
        "custom:roles": "engineer,qa_manager",
        "custom:programs": "ALPHA,BRAVO",
        "email": "u@example.mil",
    })
    out = pre_token_gen.handler(event, None)

    response = out["response"]
    details = response["claimsAndScopeOverrideDetails"]
    access = details["accessTokenGeneration"]["claimsToAddOrOverride"]
    idt = details["idTokenGeneration"]["claimsToAddOrOverride"]

    assert access == {
        "custom:roles": "engineer,qa_manager",
        "custom:programs": "ALPHA,BRAVO",
    }
    assert idt == access, (
        "access and id token overrides must match so the frontend "
        "does not need a separate /users/me call to render role-"
        "aware UI"
    )


def test_missing_attributes_emit_empty_strings() -> None:
    """Fail-CLOSED: a user with no custom:roles / custom:programs
    attrs gets empty-string claims, which makes the API's permission
    + program-scope checks return 403 on every protected action.
    That's the correct outcome for a mis-synced user."""
    out = pre_token_gen.handler(_event({}), None)
    access = out["response"]["claimsAndScopeOverrideDetails"][
        "accessTokenGeneration"
    ]["claimsToAddOrOverride"]
    assert access == {
        "custom:roles": "",
        "custom:programs": "",
    }


def test_empty_attribute_strings_pass_through() -> None:
    """An explicit empty-string attribute stays empty in the token
    — no synthetic default is invented."""
    event = _event({
        "custom:roles": "",
        "custom:programs": "",
    })
    out = pre_token_gen.handler(event, None)
    access = out["response"]["claimsAndScopeOverrideDetails"][
        "accessTokenGeneration"
    ]["claimsToAddOrOverride"]
    assert access == {"custom:roles": "", "custom:programs": ""}


def test_non_string_attribute_values_coerced() -> None:
    """Defensive: AWS SDKs sometimes marshal attributes as non-string
    types.  The handler must stringify safely rather than 500."""
    event = _event({
        # Simulate a coercion edge case.  Cognito typically sends
        # strings, but a misconfigured test harness could surface
        # None.  The handler treats None as empty, not a crash.
        "custom:roles": None,  # type: ignore[dict-item]
        "custom:programs": "ONLY",
    })
    out = pre_token_gen.handler(event, None)
    access = out["response"]["claimsAndScopeOverrideDetails"][
        "accessTokenGeneration"
    ]["claimsToAddOrOverride"]
    assert access == {"custom:roles": "", "custom:programs": "ONLY"}


def test_unknown_attributes_ignored() -> None:
    """Extra user attributes the handler doesn't know about must
    not leak into the token claim override."""
    event = _event({
        "custom:roles": "operator",
        "custom:programs": "FOO",
        "custom:favorite_color": "red",
        "email": "leak@example.mil",
    })
    out = pre_token_gen.handler(event, None)
    access = out["response"]["claimsAndScopeOverrideDetails"][
        "accessTokenGeneration"
    ]["claimsToAddOrOverride"]
    assert set(access.keys()) == {"custom:roles", "custom:programs"}


@pytest.mark.parametrize("trigger", [
    "TokenGeneration_Authentication",
    "TokenGeneration_RefreshTokens",
    "TokenGeneration_HostedAuth",
    "TokenGeneration_NewPasswordChallenge",
])
def test_all_supported_trigger_sources_behave_identically(
    trigger: str,
) -> None:
    """Spec §5.0.4 — claims must land on EVERY token the pool issues,
    not just initial authentication.  The handler must not inspect
    triggerSource to decide whether to inject."""
    event = _event(
        {"custom:roles": "inspector", "custom:programs": "GAMMA"},
        trigger_source=trigger,
    )
    out = pre_token_gen.handler(event, None)
    access = out["response"]["claimsAndScopeOverrideDetails"][
        "accessTokenGeneration"
    ]["claimsToAddOrOverride"]
    assert access["custom:roles"] == "inspector"
    assert access["custom:programs"] == "GAMMA"
