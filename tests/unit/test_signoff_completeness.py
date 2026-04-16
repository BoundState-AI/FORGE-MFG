#!/usr/bin/env python3
"""
Module:   Test Sign-Off Completeness Rules
Purpose:  Pin the COMP-003 + SOD-005 behaviors that GC-3 depends on:
          current-cycle-only filtering and safety-critical distinct-user
          enforcement.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  GC-3 narrows protected actions to safety-critical sign-off and NCR
  disposition.  The dual-signoff completeness rule is what backs the
  same-device sequential UX defined in UX spec §9.2:

    * Only sign-offs in the current execution_cycle satisfy completion.
      A reopened-and-completed-again operation must accumulate FRESH
      sign-offs in the new cycle; prior-cycle sign-offs become audit
      history only.
    * For safety-critical operations, the union of user_ids across
      required roles must equal the number of required roles — i.e.
      no single user can satisfy two roles, even if they hold both
      role assignments (multi-role edge case in §9.4).

  These behaviors live in the pure rule evaluator
  ``check_comp_003_signoffs_complete``.  The integration suite
  (``test_inspection_signoff.py``) exercises them through the HTTP
  layer.  This unit test is the regression guard for the rule itself
  so a future refactor of the evaluator cannot silently weaken
  current-cycle scoping or distinct-user enforcement.
"""

from __future__ import annotations

import uuid

import pytest

from forge.rules.compliance_checks import (
    SignOffSnapshot,
    check_comp_003_signoffs_complete,
)


def _signoff(
    user_id: uuid.UUID, role: str, cycle: int,
) -> SignOffSnapshot:
    """Helper for terse fixture construction."""
    return SignOffSnapshot(user_id=user_id, role=role, execution_cycle=cycle)


class TestCompletenessCurrentCycle:
    """COMP-003 must only count current-cycle sign-offs."""

    def test_prior_cycle_signoffs_do_not_satisfy_current_cycle(self) -> None:
        """A reopened op (cycle 2) cannot be completed by relying on
        sign-offs from cycle 1 — those are audit history only.

        This is the core invariant that makes the GC-2 reopen flow
        sound: rework starts a fresh sign-off requirement; prior
        attestations do not transfer to the new cycle.
        """
        op_user = uuid.uuid4()
        insp_user = uuid.uuid4()
        violation = check_comp_003_signoffs_complete(
            sign_off_roles_required=["operator", "inspector"],
            safety_critical=True,
            current_cycle=2,
            sign_offs=[
                # Both required sign-offs exist, but they are from cycle 1
                # (the original execution before the reopen).
                _signoff(op_user, "operator", cycle=1),
                _signoff(insp_user, "inspector", cycle=1),
            ],
        )
        assert violation is not None, "prior-cycle sign-offs cannot satisfy current cycle"
        assert violation.rule_id == "COMP-003"
        assert "operator" in violation.message
        assert "inspector" in violation.message

    def test_current_cycle_signoffs_satisfy_completion(self) -> None:
        """Sanity check: with the same shape but current-cycle sign-offs,
        the rule passes."""
        op_user = uuid.uuid4()
        insp_user = uuid.uuid4()
        violation = check_comp_003_signoffs_complete(
            sign_off_roles_required=["operator", "inspector"],
            safety_critical=True,
            current_cycle=2,
            sign_offs=[
                _signoff(op_user, "operator", cycle=2),
                _signoff(insp_user, "inspector", cycle=2),
            ],
        )
        assert violation is None


class TestCompletenessSod005Distinct:
    """COMP-003 enforces distinct user_ids on safety-critical ops."""

    def test_same_user_dual_role_rejected_on_safety_critical(self) -> None:
        """One user holding both required roles cannot satisfy
        completion on a safety-critical op.  This pins the multi-role
        edge case from UX spec §9.4."""
        sole_user = uuid.uuid4()
        violation = check_comp_003_signoffs_complete(
            sign_off_roles_required=["operator", "inspector"],
            safety_critical=True,
            current_cycle=1,
            sign_offs=[
                _signoff(sole_user, "operator", cycle=1),
                _signoff(sole_user, "inspector", cycle=1),
            ],
        )
        assert violation is not None
        assert violation.rule_id == "COMP-003"
        assert "distinct user_ids" in violation.message

    def test_distinct_users_satisfy_safety_critical(self) -> None:
        """Two distinct users covering the two required roles → pass."""
        op_user = uuid.uuid4()
        insp_user = uuid.uuid4()
        violation = check_comp_003_signoffs_complete(
            sign_off_roles_required=["operator", "inspector"],
            safety_critical=True,
            current_cycle=1,
            sign_offs=[
                _signoff(op_user, "operator", cycle=1),
                _signoff(insp_user, "inspector", cycle=1),
            ],
        )
        assert violation is None

    def test_same_user_dual_role_allowed_on_non_safety_critical(self) -> None:
        """Distinct-user enforcement is gated on safety_critical.  An
        ordinary operation with both sign-offs from the same user is
        allowed; SOD-005 does not apply."""
        sole_user = uuid.uuid4()
        violation = check_comp_003_signoffs_complete(
            sign_off_roles_required=["operator", "inspector"],
            safety_critical=False,
            current_cycle=1,
            sign_offs=[
                _signoff(sole_user, "operator", cycle=1),
                _signoff(sole_user, "inspector", cycle=1),
            ],
        )
        assert violation is None


class TestCompletenessMissingRoles:
    """COMP-003 must report missing roles, not silently pass."""

    @pytest.mark.parametrize(
        "present_role, missing_role",
        [("operator", "inspector"), ("inspector", "operator")],
    )
    def test_missing_role_blocks_completion(
        self, present_role: str, missing_role: str,
    ) -> None:
        """Missing one of the required roles → block, naming the gap."""
        u = uuid.uuid4()
        violation = check_comp_003_signoffs_complete(
            sign_off_roles_required=["operator", "inspector"],
            safety_critical=True,
            current_cycle=1,
            sign_offs=[_signoff(u, present_role, cycle=1)],
        )
        assert violation is not None
        assert missing_role in violation.message
