#!/usr/bin/env python3
"""
Module:   Compliance Check Rules
Purpose:  Stateless evaluators for spec §4.4 COMP-001 and COMP-003 checks
          required at operation completion.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  COMP-001 (inspection hold) and COMP-003 (sign-off completeness) are
  the gates that protect operation completion from skipping required
  quality steps.  Like the SoD rules, they are stateless evaluators —
  the service layer hydrates the events from the current execution
  cycle and asks: is completion allowed?

  Keeping the cycle filter in the rule (rather than the caller) makes
  it impossible for a future service to forget to apply it.  An old
  inspection from a prior execution_cycle is, by spec, no longer a
  valid quality gate after rework.

  COMP-002, COMP-004, COMP-005 are material/document rules and
  live with their domains, not here.
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ComplianceViolation:
    """Structured compliance violation matching the §5.15 error envelope."""

    rule_id: str
    rule_name: str
    severity: str
    message: str
    context: dict[str, object]


@dataclass(frozen=True, slots=True)
class InspectionSnapshot:
    """Minimal inspection projection for COMP-001 evaluation.

    Pure data so the rule is testable without a database.  result is the
    raw enum value string ("accept", "reject", "conditional").
    """

    inspector_id: _uuid.UUID
    execution_cycle: int
    result: str


@dataclass(frozen=True, slots=True)
class SignOffSnapshot:
    """Minimal sign-off projection for COMP-003 evaluation."""

    user_id: _uuid.UUID
    role: str
    execution_cycle: int


# ---------------------------------------------------------------------------
# COMP-001: Inspection hold enforcement
# ---------------------------------------------------------------------------


def check_comp_001_inspection_hold(
    *,
    inspection_hold: bool,
    current_cycle: int,
    inspections: Iterable[InspectionSnapshot],
) -> ComplianceViolation | None:
    """COMP-001: inspection hold requires accepted inspection in current cycle.

    Spec §4.4: "Inspection hold enforcement — requires accepted
    InspectionEvent from current execution_cycle."

    State leaving: in_progress.  State entering: complete.
    Invariant: if the operation has inspection_hold=True, at least one
    inspection event with result="accept" from the current
    execution_cycle must exist before completion is allowed.

    Why current_cycle filter: rework increments execution_cycle, so prior
    inspections remain in the audit trail but no longer satisfy the gate.
    """
    if not inspection_hold:
        return None

    accepted_in_cycle = [
        i for i in inspections
        if i.execution_cycle == current_cycle and i.result == "accept"
    ]
    if accepted_in_cycle:
        return None

    return ComplianceViolation(
        rule_id="COMP-001",
        rule_name="Inspection hold enforcement",
        severity="block",
        message=(
            "Operation has an inspection hold and no accepted inspection "
            "exists for the current execution cycle"
        ),
        context={"execution_cycle": current_cycle},
    )


# ---------------------------------------------------------------------------
# COMP-003: Required sign-offs complete
# ---------------------------------------------------------------------------


def check_comp_003_signoffs_complete(
    *,
    sign_off_roles_required: Iterable[str],
    safety_critical: bool,
    current_cycle: int,
    sign_offs: Iterable[SignOffSnapshot],
) -> ComplianceViolation | None:
    """COMP-003: all required sign-off roles must have current-cycle events.

    Spec §4.4: "Required sign-offs complete — all sign_off_roles_required
    must have SignOffEvents from current execution_cycle.  For
    safety-critical ops, distinct user_ids enforced by SOD-005."

    State leaving: in_progress.  State entering: complete.
    Invariants (in order):
    1. Each role in sign_off_roles_required must have at least one
       sign-off event in the current execution_cycle.
    2. For safety-critical operations, the union of user_ids across
       roles must contain at least len(roles) distinct users — i.e.
       no single user can satisfy two roles.
    """
    required = list(sign_off_roles_required)
    if not required:
        return None

    same_cycle = [s for s in sign_offs if s.execution_cycle == current_cycle]
    by_role: dict[str, list[SignOffSnapshot]] = {}
    for s in same_cycle:
        by_role.setdefault(s.role, []).append(s)

    missing = [r for r in required if r not in by_role]
    if missing:
        return ComplianceViolation(
            rule_id="COMP-003",
            rule_name="Required sign-offs complete",
            severity="block",
            message=(
                f"Missing sign-offs for required role(s): {', '.join(missing)}"
            ),
            context={
                "missing_roles": missing,
                "execution_cycle": current_cycle,
            },
        )

    if safety_critical:
        # SOD-005 distinctness: the set of user_ids that have signed any
        # of the required roles in the current cycle must be at least
        # as large as the number of required roles.  This catches the
        # case where one user holds multiple roles and signed them all.
        signers: set[_uuid.UUID] = set()
        for role in required:
            for s in by_role[role]:
                signers.add(s.user_id)
        if len(signers) < len(required):
            return ComplianceViolation(
                rule_id="COMP-003",
                rule_name="Required sign-offs complete",
                severity="block",
                message=(
                    "Safety-critical operation requires distinct user_ids "
                    "across all required sign-off roles (SOD-005)"
                ),
                context={
                    "required_roles": required,
                    "distinct_signers": len(signers),
                    "execution_cycle": current_cycle,
                },
            )

    return None
