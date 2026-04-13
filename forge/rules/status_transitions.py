#!/usr/bin/env python3
"""
Module:   Status Transitions
Purpose:  Enforce valid state machine transitions for work orders and operations.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Status transitions are the primary enforcement boundary for
  manufacturing execution correctness.  The spec §4.3 defines exact
  transition maps — no additional states or transitions are permitted.
  This module is a stateless evaluator: it receives current state and
  target state, returns allowed/denied.  No DB access, no side effects.
"""

from __future__ import annotations

from forge.models.enums import OpStatus, WOStatus

# Spec §4.3: valid work order transitions
VALID_WO_TRANSITIONS: dict[WOStatus, set[WOStatus]] = {
    WOStatus.draft: {WOStatus.released},
    WOStatus.released: {WOStatus.in_progress, WOStatus.hold},
    WOStatus.in_progress: {WOStatus.complete, WOStatus.hold},
    WOStatus.hold: {WOStatus.in_progress, WOStatus.closed},
    WOStatus.complete: {WOStatus.closed},
    WOStatus.closed: set(),
}

# Spec §4.3: valid operation transitions
VALID_OP_TRANSITIONS: dict[OpStatus, set[OpStatus]] = {
    OpStatus.pending: {OpStatus.in_progress},
    OpStatus.in_progress: {OpStatus.complete, OpStatus.hold, OpStatus.rework},
    OpStatus.hold: {OpStatus.in_progress},
    OpStatus.rework: {OpStatus.in_progress},
    OpStatus.complete: set(),
}


def validate_wo_transition(current: WOStatus, target: WOStatus) -> bool:
    """Check if a work order transition is valid per spec §4.3.

    State leaving: current.  State entering: target.
    Invariant: target must be in the set of valid successors for current.

    Why .get(current, set()): unknown states return an empty set, which
    correctly rejects all transitions from an invalid starting state
    rather than raising a KeyError.
    """
    return target in VALID_WO_TRANSITIONS.get(current, set())


def validate_op_transition(current: OpStatus, target: OpStatus) -> bool:
    """Check if an operation transition is valid per spec §4.3.

    State leaving: current.  State entering: target.
    Invariant: target must be in the set of valid successors for current.
    """
    return target in VALID_OP_TRANSITIONS.get(current, set())
