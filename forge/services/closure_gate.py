#!/usr/bin/env python3
"""
Module:   Closure Gate
Purpose:  Single authoritative check for the Phase-1 closure-as-
          attestation feature flag (UX spec v1.5.1 §5.3, §5.6.7).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why a dedicated module:
  Both `close_work_order` and `close_ncr` need to honor the same
  governance gate before any mutation lands.  Centralising the check
  here prevents drift — there is exactly one place to read the flag
  and exactly one error envelope shape callers branch on.

Why 403 (not 503):
  The gate is a deliberate governance posture, not a transient
  service outage.  Spec §5.0.2 establishes the precedent: feature-
  flag-blocked endpoints return 403 with a stable machine-readable
  code (`FEATURE_DISABLED`).  We follow the same posture with
  `CLOSURE_DISABLED` so the frontend's existing 403-handling can
  branch on `code` without inventing a new HTTP-status fork.

Why no audit row on the rejected attempt:
  A `CLOSURE_DISABLED` rejection is not an attestation event — the
  user did not perform an operation against the entity, so there is
  nothing to attest.  The gate fires BEFORE any state mutation OR
  audit-write side effect; the audit chain stays a record of
  *operations performed*, not *operations blocked at the door*.
"""

from __future__ import annotations

from fastapi import HTTPException

from forge.config import Settings, get_settings


def check_closure_enabled(settings: Settings | None = None) -> None:
    """Raise 403 CLOSURE_DISABLED if the closure feature flag is off.

    Returns None when closure is enabled — the caller proceeds to
    enforce the entity-specific preconditions (NCRs closed, evidence
    present, remediation WO closed, etc.).

    `settings` is keyword-only and optional so call sites in service
    code can pass a per-request settings instance (useful for
    test isolation when ``Settings(...)`` is constructed explicitly
    in a fixture).  When omitted, falls back to the cached
    ``get_settings()`` singleton — the production path.
    """
    cfg = settings or get_settings()
    if cfg.closure_enabled:
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "CLOSURE_DISABLED",
            "message": (
                "Closure is disabled in this environment.  Phase-1 "
                "close endpoints require MSFC QA approval (or a "
                "formal QA waiver) before enablement; until then, "
                "the effective terminal states are `complete` (work "
                "orders) and `dispositioned` (NCRs)."
            ),
        },
    )
