#!/usr/bin/env python3
"""
Module:   Test Config Production Safety
Purpose:  Pin the Settings production-safety validator against unsafe boot configs.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  forge.config.Settings._enforce_production_safety is the last gate between a
  misconfigured environment and a GovCloud deploy that silently hands every
  caller every permission.  AuthMiddleware.dispatch injects a fully-permissioned
  local dev AuthContext when auth_mode != "cognito" (forge/api/middleware/auth.py
  lines 80-96); Build Spec §9.3.6 mandates Cognito-backed session security in
  production and staging.  These tests pin:

    - production + auth_mode=local  → rejected (GovCloud blast radius)
    - staging    + auth_mode=local  → rejected (staging mirrors production, §9.3.7)
    - production + auth_mode=cognito → accepted (the intended prod path)
    - local      + auth_mode=local  → accepted (dev ergonomics still work)
    - production + debug=True        → rejected (regression guard for the
      pre-existing debug invariant — adding a second condition must not mask
      the first)

  The validator runs in Pydantic's "after" mode, so ValidationError wraps the
  ValueError.  We assert on ValidationError and inspect its string form to
  confirm the offending field is named — a reviewer reading the boot log must
  be able to identify which invariant tripped without a debugger.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge.config import Settings

# ---------------------------------------------------------------------------
# Red paths — unsafe configurations must raise at model construction
# ---------------------------------------------------------------------------


def test_production_with_local_auth_mode_is_rejected() -> None:
    """production + auth_mode=local → ValidationError naming auth_mode and env.

    Why: this is the exact misconfiguration that would ship local-dev's
    fully-permissioned AuthContext to GovCloud.  The error message must name
    both fields so an operator reading the container crash log sees the fix.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(env="production", auth_mode="local")

    message = str(exc_info.value)
    assert "auth_mode" in message
    assert "production" in message
    assert "cognito" in message


def test_staging_with_local_auth_mode_is_rejected() -> None:
    """staging + auth_mode=local → ValidationError.

    Why: Build Spec §9.3.7 states staging mirrors production.  If staging
    accepted local auth mode, a pre-production smoke test could run against a
    wide-open API and appear to pass, masking a prod-bound regression.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(env="staging", auth_mode="local")

    message = str(exc_info.value)
    assert "auth_mode" in message
    assert "staging" in message
    assert "cognito" in message


def test_production_with_debug_true_still_rejected() -> None:
    """Regression guard: the pre-existing debug invariant must still fire.

    Why: adding the auth_mode condition to the same validator must not mask
    the debug invariant.  We pin auth_mode=cognito here so only the debug
    condition can trip — proving the debug gate still works on its own.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(env="production", debug=True, auth_mode="cognito")

    message = str(exc_info.value)
    assert "debug" in message
    assert "production" in message


# ---------------------------------------------------------------------------
# Happy paths — safe configurations must construct cleanly
# ---------------------------------------------------------------------------


def test_production_with_cognito_auth_mode_succeeds() -> None:
    """The intended production configuration boots cleanly.

    Why: if this test fails, the validator is over-broad and would block the
    real deploy path.  debug defaults to False, so only the auth_mode
    invariant is in play.
    """
    settings = Settings(env="production", auth_mode="cognito")

    assert settings.env == "production"
    assert settings.auth_mode == "cognito"
    assert settings.debug is False


def test_local_env_with_local_auth_mode_succeeds() -> None:
    """Local development must continue to boot without Cognito.

    Why: forcing cognito in local would break every developer's workstation
    and the entire test suite (which runs under auth_mode=local via the
    AuthMiddleware pass-through).  The invariant is env-gated for a reason.
    """
    settings = Settings(env="local", auth_mode="local")

    assert settings.env == "local"
    assert settings.auth_mode == "local"
