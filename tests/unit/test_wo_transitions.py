#!/usr/bin/env python3
"""
Module:   Test Work Order Status Transitions
Purpose:  Pin the WO state-machine edges the spec actually permits and
          the ones it explicitly does not.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why these tests:
  The WO close transition is the load-bearing terminal state for QA
  attestation (UX spec §5.3 / Build spec §4.3, §5.1).  Earlier code
  permitted ``hold → closed`` directly; the frozen Phase-1 contract
  is ``complete → closed`` only — closing from `hold` skips the
  completion-completeness invariants the QA gate is supposed to
  attest.  This test pins both directions so a future refactor of
  ``VALID_WO_TRANSITIONS`` cannot silently re-open the path.
"""

from __future__ import annotations

import pytest

from forge.models.enums import WOStatus
from forge.rules.status_transitions import (
    VALID_WO_TRANSITIONS,
    validate_wo_transition,
)


class TestWOClosePath:
    """`closed` is reachable only from `complete` (GC-4)."""

    def test_complete_to_closed_allowed(self) -> None:
        assert validate_wo_transition(
            WOStatus.complete, WOStatus.closed,
        )

    def test_hold_to_closed_rejected(self) -> None:
        """A WO on `hold` cannot close directly.

        The spec's QA-gate close is an attestation that the WO
        actually completed.  Allowing `hold → closed` would let an
        operator paper over a problem that put the WO on hold by
        skipping `resume → complete → close`.
        """
        assert not validate_wo_transition(
            WOStatus.hold, WOStatus.closed,
        )

    def test_in_progress_to_closed_rejected(self) -> None:
        assert not validate_wo_transition(
            WOStatus.in_progress, WOStatus.closed,
        )

    def test_released_to_closed_rejected(self) -> None:
        assert not validate_wo_transition(
            WOStatus.released, WOStatus.closed,
        )

    def test_draft_to_closed_rejected(self) -> None:
        assert not validate_wo_transition(
            WOStatus.draft, WOStatus.closed,
        )

    def test_closed_is_terminal(self) -> None:
        assert VALID_WO_TRANSITIONS[WOStatus.closed] == set()


class TestWOHoldExits:
    """`hold` keeps `resume → in_progress` as its only exit (GC-4)."""

    def test_hold_to_in_progress_allowed(self) -> None:
        assert validate_wo_transition(
            WOStatus.hold, WOStatus.in_progress,
        )

    def test_hold_outgoing_set_is_resume_only(self) -> None:
        """Defensive: `hold → closed` is not in the outgoing set."""
        assert VALID_WO_TRANSITIONS[WOStatus.hold] == {WOStatus.in_progress}


@pytest.mark.parametrize(
    "current,target,expected",
    [
        # Normal lifecycle
        (WOStatus.draft, WOStatus.released, True),
        (WOStatus.released, WOStatus.in_progress, True),
        (WOStatus.in_progress, WOStatus.complete, True),
        (WOStatus.complete, WOStatus.closed, True),
        # GC-2 reactivation: `complete → in_progress` still allowed
        # so an NCR-driven reopen can drop the WO back to active.
        (WOStatus.complete, WOStatus.in_progress, True),
        # Hold / resume
        (WOStatus.in_progress, WOStatus.hold, True),
        (WOStatus.hold, WOStatus.in_progress, True),
        # Recall to draft (pre-activation only)
        (WOStatus.released, WOStatus.draft, True),
        # GC-4 explicitly rejected edges
        (WOStatus.hold, WOStatus.closed, False),
        (WOStatus.in_progress, WOStatus.closed, False),
        (WOStatus.draft, WOStatus.closed, False),
        # Closed is terminal
        (WOStatus.closed, WOStatus.in_progress, False),
        (WOStatus.closed, WOStatus.complete, False),
    ],
)
def test_wo_transition_truth_table(
    current: WOStatus, target: WOStatus, expected: bool,
) -> None:
    assert validate_wo_transition(current, target) is expected
