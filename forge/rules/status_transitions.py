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

from forge.models.enums import (
    CertStatus,
    NCRStatus,
    OpStatus,
    PartStatus,
    ScheduleStatus,
    WOStatus,
)

# Spec §4.3 + UX spec v1.5.1 §5.3: valid work order transitions.
# `released → draft` is the Recall transition (slice 5B-14), used
# pre-activation to return a WO to draft for graph correction before
# any operation has started.  The "no op has started" invariant is
# inherent in the source state: the first op start atomically moves
# the WO to in_progress (see start_operation), so if we are still
# in released, no op has started.  That preserves the spec §5.3
# precondition "WO is in released state (no operations have been
# started; if any had, WO would be in_progress)" without an
# additional runtime check.
VALID_WO_TRANSITIONS: dict[WOStatus, set[WOStatus]] = {
    WOStatus.draft: {WOStatus.released},
    WOStatus.released: {WOStatus.in_progress, WOStatus.hold, WOStatus.draft},
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


# Spec §3.1 NonconformanceReport status block:
#
#     open → dispositioned → closed
#          → voided
#
# Why explicit map: keeping the transition policy here (not inline in the
# service) means a NASA reviewer can audit every legal NCR move from one
# place.  CLAUDE.md State-And-Permission Alignment forbids reintroducing
# rejected states ("under_review", "verified") — those names must never
# appear in this dict, and `tests/unit/test_models.py` already locks the
# enum vocabulary itself.
VALID_NCR_TRANSITIONS: dict[NCRStatus, set[NCRStatus]] = {
    NCRStatus.open: {NCRStatus.dispositioned, NCRStatus.voided},
    NCRStatus.dispositioned: {NCRStatus.closed},
    NCRStatus.closed: set(),
    NCRStatus.voided: set(),
}


def validate_ncr_transition(current: NCRStatus, target: NCRStatus) -> bool:
    """Check if an NCR status transition is valid per spec §3.1.

    State leaving: current.  State entering: target.
    Invariant: target must be in the set of valid successors for current.

    Why .get(current, set()): an unknown starting state correctly rejects
    every transition rather than raising — defense against future enum
    additions that forget to extend the map.
    """
    return target in VALID_NCR_TRANSITIONS.get(current, set())


# Spec §3.1 MaterialCertification lifecycle (implicit — enum given, flow
# inferred from the receive/accept/consume endpoints and spec §4.4 COMP-002
# "status must be accepted and not past shelf_life_expiry").
#
#     quarantine ─► accepted ─► consumed
#                │           └► expired
#                ├► rejected (terminal)
#                └► expired  (terminal; shelf-life)
#
# Why explicit map: reviewers should see the whole cert lifecycle in one
# place.  `rejected`, `expired`, and `consumed` are terminal — a rejected
# lot must never be resurrected, and once consumed (fully exhausted) the
# lot is closed.  Expiry is reached via shelf_life_expiry elapse and is
# typically set by a background job; for Phase 1 the transition target is
# reachable administratively via status updates if needed, but consume
# itself treats past-expiry as blocked whether or not the row says so.
VALID_CERT_TRANSITIONS: dict[CertStatus, set[CertStatus]] = {
    CertStatus.quarantine: {
        CertStatus.accepted, CertStatus.rejected, CertStatus.expired,
    },
    CertStatus.accepted: {CertStatus.consumed, CertStatus.expired},
    CertStatus.rejected: set(),
    CertStatus.expired: set(),
    CertStatus.consumed: set(),
}


def validate_cert_transition(
    current: CertStatus, target: CertStatus
) -> bool:
    """Check if a material-certification transition is valid.

    State leaving: current.  State entering: target.
    Invariant: target must be in the set of valid successors for current.
    """
    return target in VALID_CERT_TRANSITIONS.get(current, set())


# Spec §3.1 PartInstance lifecycle (SPEC AMBIGUITY: enum defined, no
# explicit transition map given).  This map reflects the physically
# meaningful flow for a serialized manufactured part:
#
#     raw ─► in_process ─► completed ─► installed
#       │              └─────────────┬─► scrapped
#       └─► quarantined ◄────────────┘
#                │
#                └─► in_process  (quarantine release)
#                └─► scrapped
#
# Why this map: a raw part may go into production, be scrapped for
# material/gauge failures, or be quarantined pending review.  A
# quarantined part may be released back to in_process or condemned.
# `scrapped` is terminal.  `installed` → `scrapped` is allowed for
# post-install tear-downs (engine disassembly / field returns) but no
# other transition out of installed is permitted without a new assembly.
VALID_PART_TRANSITIONS: dict[PartStatus, set[PartStatus]] = {
    PartStatus.raw: {
        PartStatus.in_process, PartStatus.scrapped, PartStatus.quarantined,
    },
    PartStatus.in_process: {
        PartStatus.completed, PartStatus.scrapped, PartStatus.quarantined,
    },
    PartStatus.completed: {
        PartStatus.installed, PartStatus.scrapped, PartStatus.quarantined,
    },
    PartStatus.installed: {PartStatus.scrapped},
    PartStatus.quarantined: {PartStatus.in_process, PartStatus.scrapped},
    PartStatus.scrapped: set(),
}


def validate_part_transition(
    current: PartStatus, target: PartStatus
) -> bool:
    """Check if a part-instance status transition is valid.

    State leaving: current.  State entering: target.
    Invariant: target must be in the set of valid successors for current.
    """
    return target in VALID_PART_TRANSITIONS.get(current, set())


# Spec §3.1 BuildSchedule lifecycle (enum given; transitions implicit):
#
#     draft ─► baselined ─► in_progress ─► complete
#
# Why explicit map: baseline freezes the schedule for execution — a
# baselined schedule must not slip back to draft (editing history would
# break the audit trail).  `in_progress` and `complete` are reached via
# outbox-driven recalculation in 4B; 4A only exposes the draft→baselined
# transition (`POST /api/schedules/{id}/baseline`).  The transitions are
# defined here now so 4B can plug in without reshaping the policy.
VALID_SCHEDULE_TRANSITIONS: dict[ScheduleStatus, set[ScheduleStatus]] = {
    ScheduleStatus.draft: {ScheduleStatus.baselined},
    ScheduleStatus.baselined: {ScheduleStatus.in_progress},
    ScheduleStatus.in_progress: {ScheduleStatus.complete},
    ScheduleStatus.complete: set(),
}


def validate_schedule_transition(
    current: ScheduleStatus, target: ScheduleStatus,
) -> bool:
    """Check if a BuildSchedule transition is valid.

    State leaving: current.  State entering: target.
    Invariant: target must be in the set of valid successors for current.
    """
    return target in VALID_SCHEDULE_TRANSITIONS.get(current, set())
