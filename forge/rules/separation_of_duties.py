#!/usr/bin/env python3
"""
Module:   Separation-of-Duties Rules
Purpose:  Stateless evaluators for spec §4.2 SoD rules SOD-001..SOD-005.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Per spec §4.1, the rules engine is a *stateless evaluator*: it receives
  fully hydrated context and returns a decision.  No DB access, no I/O,
  no side effects.  Service layers gather the inputs (labor entries,
  prior sign-off events, etc.) and call these helpers to make the
  block/allow decision.  Keeping the evaluation pure makes SoD logic
  auditable, easy to unit-test, and resistant to hidden coupling that
  has historically hidden SoD bypasses at transition boundaries.

  Each rule is one focused function with a single block-condition.
  All five SoD rules from spec §4.2 live here so a NASA reviewer can
  read the entire SoD policy surface in one file.
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SoDViolation:
    """Structured SoD violation suitable for the standard error envelope.

    Mirrors spec §5.15 RuleViolation shape so service-layer code can hand
    this directly into the 422 RULE_VIOLATION response body without
    additional reshaping.
    """

    rule_id: str
    rule_name: str
    severity: str
    message: str
    context: dict[str, object]


# ---------------------------------------------------------------------------
# SOD-001: Self-inspection prohibition
# ---------------------------------------------------------------------------


def check_sod_001_self_inspection(
    *,
    inspector_id: _uuid.UUID,
    labor_user_ids: Iterable[_uuid.UUID],
) -> SoDViolation | None:
    """SOD-001: inspector must not have labor entries on the operation.

    Spec §4.2: "Self-inspection prohibition — inspector must not have
    labor entries on the operation."

    Why this gate exists: an operator who performed the work cannot
    independently judge its quality.  Independence is the audit guarantee.

    Returns a violation if inspector_id appears in labor_user_ids;
    otherwise returns None (allowed).
    """
    if inspector_id in set(labor_user_ids):
        return SoDViolation(
            rule_id="SOD-001",
            rule_name="Self-inspection prohibition",
            severity="block",
            message=(
                "Inspector cannot also have performed labor on this operation"
            ),
            context={"inspector_id": str(inspector_id)},
        )
    return None


# ---------------------------------------------------------------------------
# SOD-002: NCR disposition independence
# ---------------------------------------------------------------------------


def check_sod_002_ncr_disposition(
    *,
    initiator_id: _uuid.UUID,
    dispositioner_id: _uuid.UUID,
) -> SoDViolation | None:
    """SOD-002: the NCR initiator cannot disposition the same NCR.

    Spec §4.2: "NCR disposition independence — initiator cannot
    disposition the same NCR."

    Co-located with the other four SoD evaluators so all five live
    against a single source of policy and share the same unit tests.
    """
    if initiator_id == dispositioner_id:
        return SoDViolation(
            rule_id="SOD-002",
            rule_name="NCR disposition independence",
            severity="block",
            message="The NCR initiator cannot also disposition the NCR",
            context={"initiator_id": str(initiator_id)},
        )
    return None


# ---------------------------------------------------------------------------
# SOD-003: Material acceptance independence
# ---------------------------------------------------------------------------


def check_sod_003_material_acceptance(
    *,
    receiver_id: _uuid.UUID,
    accepter_id: _uuid.UUID,
) -> SoDViolation | None:
    """SOD-003: the material receiver cannot also be the acceptance inspector.

    Spec §4.2: "Material acceptance independence — receiver cannot be
    the acceptance inspector."

    Co-located with the other SoD evaluators for shared policy and
    shared unit-test coverage.
    """
    if receiver_id == accepter_id:
        return SoDViolation(
            rule_id="SOD-003",
            rule_name="Material acceptance independence",
            severity="block",
            message="The material receiver cannot also accept the material",
            context={"receiver_id": str(receiver_id)},
        )
    return None


# ---------------------------------------------------------------------------
# SOD-004: Work-order release independence  (enforced in 2A)
# ---------------------------------------------------------------------------


def check_sod_004_wo_release(
    *,
    author_id: _uuid.UUID,
    releaser_id: _uuid.UUID,
) -> SoDViolation | None:
    """SOD-004: the WO author cannot release the same WO.

    Spec §4.2 / §5.1 line 885.  Already enforced inline in
    `work_order_service.release_work_order`; this helper is provided so
    the policy can be expressed once and unit-tested against a single
    source of truth.
    """
    if author_id == releaser_id:
        return SoDViolation(
            rule_id="SOD-004",
            rule_name="Work order release independence",
            severity="block",
            message="The work order author cannot also release it",
            context={"author_id": str(author_id)},
        )
    return None


# ---------------------------------------------------------------------------
# SOD-005: Dual sign-off for safety-critical operations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignOffSnapshot:
    """Minimal sign-off projection used by SOD-005 evaluation.

    Pure data — no SQLAlchemy or DB attached — so the rule is testable
    without a session.
    """

    user_id: _uuid.UUID
    role: str
    execution_cycle: int


def check_sod_005_dual_signoff(
    *,
    safety_critical: bool,
    current_cycle: int,
    sign_offs: Iterable[SignOffSnapshot],
    new_user_id: _uuid.UUID,
    new_role: str,
) -> SoDViolation | None:
    """SOD-005: dual sign-off requires distinct users across roles.

    Spec §4.2: "Dual sign-off for safety-critical operations — requires
    both operator and independent inspector sign-off from distinct
    user_ids.  A single user holding multiple roles cannot satisfy both.
    Only sign-offs matching the current execution_cycle are considered."

    The block condition we enforce *at sign-off creation* is the
    distinct-user invariant: the new sign-off cannot reuse a user_id
    already present on a *different* role in the current cycle.  The
    same user re-signing the same role is handled by the unique
    constraint on (operation_id, user_id, role, execution_cycle) and
    is not an SoD violation.

    Cycle-completeness (both required roles present) is enforced by
    COMP-003 at completion time, not here.
    """
    if not safety_critical:
        return None

    same_cycle = [s for s in sign_offs if s.execution_cycle == current_cycle]
    for prior in same_cycle:
        if prior.user_id == new_user_id and prior.role != new_role:
            return SoDViolation(
                rule_id="SOD-005",
                rule_name="Dual sign-off independence",
                severity="block",
                message=(
                    "A single user cannot satisfy both sign-off roles on a "
                    "safety-critical operation"
                ),
                context={
                    "user_id": str(new_user_id),
                    "new_role": new_role,
                    "existing_role": prior.role,
                    "execution_cycle": current_cycle,
                },
            )
    return None
