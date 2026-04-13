#!/usr/bin/env python3
"""
Module:   Test NCR Transitions
Purpose:  Unit tests for VALID_NCR_TRANSITIONS — locks the NCR state
          vocabulary and legal moves to spec §3.1.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  CLAUDE.md "Do not advance" gate on slice 3A: the words "under_review"
  and "verified" must never appear in the NCR vocabulary, and only the
  spec §3.1 transitions may be legal.  These tests pin both invariants
  so a future enum addition cannot silently slip a banned state past
  review.
"""

from __future__ import annotations

from forge.models.enums import NCRStatus
from forge.rules.status_transitions import (
    VALID_NCR_TRANSITIONS,
    validate_ncr_transition,
)

# ---------------------------------------------------------------------------
# Vocabulary lock
# ---------------------------------------------------------------------------


def test_ncr_status_vocabulary_is_only_spec_states() -> None:
    """Spec §3.1: NCR statuses are exactly open, dispositioned, closed, voided."""
    assert {s.value for s in NCRStatus} == {
        "open", "dispositioned", "closed", "voided",
    }


def test_no_banned_state_names_in_enum() -> None:
    """CLAUDE.md gate: under_review and verified must never appear."""
    names = {s.value for s in NCRStatus}
    assert "under_review" not in names
    assert "verified" not in names


def test_no_banned_state_names_in_transition_map() -> None:
    """The transition map must reference only spec-allowed states."""
    allowed = set(NCRStatus)
    for src, targets in VALID_NCR_TRANSITIONS.items():
        assert src in allowed
        for tgt in targets:
            assert tgt in allowed


# ---------------------------------------------------------------------------
# Legal moves
# ---------------------------------------------------------------------------


def test_open_to_dispositioned_allowed() -> None:
    assert validate_ncr_transition(NCRStatus.open, NCRStatus.dispositioned)


def test_open_to_voided_allowed() -> None:
    assert validate_ncr_transition(NCRStatus.open, NCRStatus.voided)


def test_dispositioned_to_closed_allowed() -> None:
    assert validate_ncr_transition(NCRStatus.dispositioned, NCRStatus.closed)


# ---------------------------------------------------------------------------
# Illegal moves
# ---------------------------------------------------------------------------


def test_open_to_closed_blocked() -> None:
    """Closing an open NCR must go through disposition first."""
    assert not validate_ncr_transition(NCRStatus.open, NCRStatus.closed)


def test_dispositioned_to_voided_blocked() -> None:
    """Spec §3.1: voided is only reachable from open."""
    assert not validate_ncr_transition(
        NCRStatus.dispositioned, NCRStatus.voided
    )


def test_closed_is_terminal() -> None:
    for tgt in NCRStatus:
        assert not validate_ncr_transition(NCRStatus.closed, tgt)


def test_voided_is_terminal() -> None:
    for tgt in NCRStatus:
        assert not validate_ncr_transition(NCRStatus.voided, tgt)
