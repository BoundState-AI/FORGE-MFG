#!/usr/bin/env python3
"""
Module:   Test Compliance Rules
Purpose:  Unit tests for spec §4.4 COMP-001 and COMP-003 evaluators.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  COMP-001 and COMP-003 are the gates that protect operation completion
  from skipping required quality steps.  Cycle-aware filtering is the
  most failure-prone aspect — these tests pin the cycle invariant so a
  future refactor cannot silently let stale events satisfy a fresh
  hold.
"""

from __future__ import annotations

import uuid

from forge.rules.compliance_checks import (
    InspectionSnapshot,
    SignOffSnapshot,
    check_comp_001_inspection_hold,
    check_comp_003_signoffs_complete,
)


def _u() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# COMP-001
# ---------------------------------------------------------------------------


def test_comp001_skipped_when_no_hold() -> None:
    v = check_comp_001_inspection_hold(
        inspection_hold=False, current_cycle=1, inspections=[]
    )
    assert v is None


def test_comp001_blocks_when_hold_with_no_inspections() -> None:
    v = check_comp_001_inspection_hold(
        inspection_hold=True, current_cycle=1, inspections=[]
    )
    assert v is not None
    assert v.rule_id == "COMP-001"


def test_comp001_blocks_when_only_rejected_inspection() -> None:
    v = check_comp_001_inspection_hold(
        inspection_hold=True,
        current_cycle=1,
        inspections=[
            InspectionSnapshot(inspector_id=_u(), execution_cycle=1, result="reject"),
        ],
    )
    assert v is not None


def test_comp001_blocks_when_accepted_inspection_is_old_cycle() -> None:
    v = check_comp_001_inspection_hold(
        inspection_hold=True,
        current_cycle=2,
        inspections=[
            InspectionSnapshot(inspector_id=_u(), execution_cycle=1, result="accept"),
        ],
    )
    assert v is not None


def test_comp001_allows_when_accepted_in_current_cycle() -> None:
    v = check_comp_001_inspection_hold(
        inspection_hold=True,
        current_cycle=2,
        inspections=[
            InspectionSnapshot(inspector_id=_u(), execution_cycle=1, result="accept"),
            InspectionSnapshot(inspector_id=_u(), execution_cycle=2, result="accept"),
        ],
    )
    assert v is None


# ---------------------------------------------------------------------------
# COMP-003
# ---------------------------------------------------------------------------


def test_comp003_skipped_when_no_required_roles() -> None:
    v = check_comp_003_signoffs_complete(
        sign_off_roles_required=[],
        safety_critical=False,
        current_cycle=1,
        sign_offs=[],
    )
    assert v is None


def test_comp003_blocks_when_role_missing() -> None:
    v = check_comp_003_signoffs_complete(
        sign_off_roles_required=["operator", "inspector"],
        safety_critical=False,
        current_cycle=1,
        sign_offs=[
            SignOffSnapshot(user_id=_u(), role="operator", execution_cycle=1),
        ],
    )
    assert v is not None
    assert v.rule_id == "COMP-003"
    assert "inspector" in v.context["missing_roles"]  # type: ignore[operator]


def test_comp003_blocks_when_signoff_is_from_old_cycle() -> None:
    v = check_comp_003_signoffs_complete(
        sign_off_roles_required=["operator"],
        safety_critical=False,
        current_cycle=2,
        sign_offs=[
            SignOffSnapshot(user_id=_u(), role="operator", execution_cycle=1),
        ],
    )
    assert v is not None


def test_comp003_allows_when_all_roles_signed_current_cycle() -> None:
    v = check_comp_003_signoffs_complete(
        sign_off_roles_required=["operator", "inspector"],
        safety_critical=False,
        current_cycle=1,
        sign_offs=[
            SignOffSnapshot(user_id=_u(), role="operator", execution_cycle=1),
            SignOffSnapshot(user_id=_u(), role="inspector", execution_cycle=1),
        ],
    )
    assert v is None


def test_comp003_safety_critical_blocks_single_user_two_roles() -> None:
    """Even if both required roles are signed, one user signing both fails."""
    same = _u()
    v = check_comp_003_signoffs_complete(
        sign_off_roles_required=["operator", "inspector"],
        safety_critical=True,
        current_cycle=1,
        sign_offs=[
            SignOffSnapshot(user_id=same, role="operator", execution_cycle=1),
            SignOffSnapshot(user_id=same, role="inspector", execution_cycle=1),
        ],
    )
    assert v is not None


def test_comp003_safety_critical_allows_distinct_signers() -> None:
    v = check_comp_003_signoffs_complete(
        sign_off_roles_required=["operator", "inspector"],
        safety_critical=True,
        current_cycle=1,
        sign_offs=[
            SignOffSnapshot(user_id=_u(), role="operator", execution_cycle=1),
            SignOffSnapshot(user_id=_u(), role="inspector", execution_cycle=1),
        ],
    )
    assert v is None
