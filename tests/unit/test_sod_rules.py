#!/usr/bin/env python3
"""
Module:   Test SoD Rules
Purpose:  Unit tests for spec §4.2 SOD-001..SOD-005 stateless evaluators.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  The SoD evaluators are pure functions.  They are the single source of
  truth for SoD policy.  Unit tests prove every rule blocks when it
  should and allows when it shouldn't, independently of any service-
  layer wiring.  Each rule has a paired allow-path test so we don't
  drift toward "blocks everything" cargo cults.
"""

from __future__ import annotations

import uuid

from forge.rules.separation_of_duties import (
    SignOffSnapshot,
    check_sod_001_self_inspection,
    check_sod_002_ncr_disposition,
    check_sod_003_material_acceptance,
    check_sod_004_wo_release,
    check_sod_005_dual_signoff,
)


def _u() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# SOD-001
# ---------------------------------------------------------------------------


def test_sod001_blocks_when_inspector_has_labor() -> None:
    inspector = _u()
    other = _u()
    v = check_sod_001_self_inspection(
        inspector_id=inspector, labor_user_ids=[other, inspector]
    )
    assert v is not None
    assert v.rule_id == "SOD-001"


def test_sod001_allows_when_inspector_has_no_labor() -> None:
    v = check_sod_001_self_inspection(
        inspector_id=_u(), labor_user_ids=[_u(), _u()]
    )
    assert v is None


def test_sod001_allows_when_no_labor_at_all() -> None:
    v = check_sod_001_self_inspection(inspector_id=_u(), labor_user_ids=[])
    assert v is None


# ---------------------------------------------------------------------------
# SOD-002 (helper for slice 3A)
# ---------------------------------------------------------------------------


def test_sod002_blocks_when_initiator_dispositions() -> None:
    same = _u()
    v = check_sod_002_ncr_disposition(initiator_id=same, dispositioner_id=same)
    assert v is not None
    assert v.rule_id == "SOD-002"


def test_sod002_allows_when_distinct_users() -> None:
    v = check_sod_002_ncr_disposition(initiator_id=_u(), dispositioner_id=_u())
    assert v is None


# ---------------------------------------------------------------------------
# SOD-003 (helper for slice 3B)
# ---------------------------------------------------------------------------


def test_sod003_blocks_when_receiver_accepts() -> None:
    same = _u()
    v = check_sod_003_material_acceptance(receiver_id=same, accepter_id=same)
    assert v is not None
    assert v.rule_id == "SOD-003"


def test_sod003_allows_when_distinct_users() -> None:
    v = check_sod_003_material_acceptance(receiver_id=_u(), accepter_id=_u())
    assert v is None


# ---------------------------------------------------------------------------
# SOD-004 (live in 2A)
# ---------------------------------------------------------------------------


def test_sod004_blocks_when_author_releases() -> None:
    same = _u()
    v = check_sod_004_wo_release(author_id=same, releaser_id=same)
    assert v is not None
    assert v.rule_id == "SOD-004"


def test_sod004_allows_when_distinct_users() -> None:
    v = check_sod_004_wo_release(author_id=_u(), releaser_id=_u())
    assert v is None


# ---------------------------------------------------------------------------
# SOD-005
# ---------------------------------------------------------------------------


def test_sod005_skipped_when_not_safety_critical() -> None:
    user = _u()
    v = check_sod_005_dual_signoff(
        safety_critical=False,
        current_cycle=1,
        sign_offs=[SignOffSnapshot(user_id=user, role="operator", execution_cycle=1)],
        new_user_id=user,
        new_role="inspector",
    )
    assert v is None


def test_sod005_blocks_same_user_different_role_same_cycle() -> None:
    user = _u()
    v = check_sod_005_dual_signoff(
        safety_critical=True,
        current_cycle=2,
        sign_offs=[SignOffSnapshot(user_id=user, role="operator", execution_cycle=2)],
        new_user_id=user,
        new_role="inspector",
    )
    assert v is not None
    assert v.rule_id == "SOD-005"


def test_sod005_allows_distinct_users_same_cycle() -> None:
    op_user, insp_user = _u(), _u()
    v = check_sod_005_dual_signoff(
        safety_critical=True,
        current_cycle=1,
        sign_offs=[
            SignOffSnapshot(user_id=op_user, role="operator", execution_cycle=1),
        ],
        new_user_id=insp_user,
        new_role="inspector",
    )
    assert v is None


def test_sod005_ignores_old_cycle_signoffs() -> None:
    """A sign-off from a prior execution_cycle does not block a new role.

    Spec §4.2: "Only sign-offs matching the current execution_cycle
    are considered."
    """
    user = _u()
    v = check_sod_005_dual_signoff(
        safety_critical=True,
        current_cycle=2,
        sign_offs=[
            SignOffSnapshot(user_id=user, role="operator", execution_cycle=1),
        ],
        new_user_id=user,
        new_role="inspector",
    )
    assert v is None
