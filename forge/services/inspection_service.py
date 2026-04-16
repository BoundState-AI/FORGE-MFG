#!/usr/bin/env python3
"""
Module:   Inspection Service
Purpose:  Business logic for recording inspections and sign-offs on
          operations, enforcing SOD-001 and SOD-005 (spec §5.4, §4.2),
          plus the Inspection Queue read-model (UX spec v1.5.1 §5.5.5).

BoundState AI 2026
Version: 0.3.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin; SoD enforcement lives here so the gate cannot be
  bypassed by a future caller that talks directly to the model layer.
  The service hydrates the rule context (labor entries for SOD-001,
  prior sign-offs for SOD-005) and delegates the decision to the pure
  evaluators in forge.rules.

  execution_cycle is derived server-side from the operation's current
  cycle — the client cannot forge a stale cycle to satisfy a hold that
  no longer applies after rework (spec §3.1).

  Audit records are written for every inspect/sign-off so the chain
  carries a tamper-evident record of who attested to what.

  `record_inspection` has two modes:
    - Normal (supersedes_inspection_id=None): op must be in_progress,
      SOD-001 applies, event is recorded against the active cycle.
    - Resolve Conditional Finding (UX spec §5.5.5): op may be
      in_progress OR complete, the new event names the conditional
      inspection it supersedes, result must be accept or reject,
      and the referenced conditional must not already be superseded.
      SOD-001 still applies.  The post-completion path is the
      mechanism that lets a WO clear a lingering conditional
      finding without reopening the op — Reopen/Reactivate remain
      provisional per §5.3.

  The queue read-model (`list_inspection_queue`) is a server-derived
  projection over Operation+WO+Program, NOT a raw InspectionEvent
  query.  Deriving state server-side keeps the frontend from having
  to reproduce COMP-001 / §5.5.5 semantics (§5.4.3 explicit
  prohibition on client-side hold-status derivation).  Superseded
  conditionals fall out of state=conditional naturally because the
  later accept/reject event has a later inspected_at, so the
  "latest in current cycle" selector picks it instead.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING to avoid the runtime import cycle:
    # forge.api.auth_freshness imports AuthContext from forge.api.auth,
    # which transitively pulls in route modules that import this file.
    # The runtime call site below imports auth_freshness lazily inside
    # the function body for the same reason.
    from forge.api.auth import AuthContext
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from forge.audit.logger import create_audit_record
from forge.models.enums import (
    ActorType,
    EvidenceScope,
    HoldPointStatus,
    InspectionQueueState,
    InspectionResult,
    OpStatus,
)
from forge.models.inspection import InspectionEvent, SignOffEvent
from forge.models.labor import LaborEntry
from forge.models.ncr import NonconformanceReport
from forge.models.operation import Operation
from forge.models.program import Program
from forge.models.work_order import WorkOrder
from forge.realtime import OPERATION_BUS, queue_publish
from forge.rules.separation_of_duties import (
    SignOffSnapshot as SoDSignOff,
)
from forge.rules.separation_of_duties import (
    SoDViolation,
    check_sod_001_self_inspection,
    check_sod_005_dual_signoff,
)
from forge.schemas.inspection import InspectionCreate, SignOffCreate
from forge.services.operation_service import (
    _derive_hold_point_status,
    _get_op,
)


def _violation_response(violation: SoDViolation) -> HTTPException:
    """Convert an SoD violation into the standard 422 RULE_VIOLATION response.

    Spec §5.15 error envelope.  Centralizing this keeps the response
    shape consistent across every SoD gate (work-order release in 2A,
    inspections/sign-offs in 2B, NCRs in 3A, materials in 3B).
    """
    return HTTPException(
        status_code=422,
        detail={
            "code": "RULE_VIOLATION",
            "message": violation.message,
            "violations": [{
                "rule_id": violation.rule_id,
                "rule_name": violation.rule_name,
                "severity": violation.severity,
                "message": violation.message,
                "context": violation.context,
            }],
        },
    )


def _require_op_in_progress(op: Operation) -> None:
    """Inspections and sign-offs are only valid while the op is in_progress.

    State leaving: in_progress.  State entering: in_progress (no
    transition — events accumulate against the active cycle).
    Invariant: the op must currently be in_progress so we are recording
    against the live execution_cycle.  Recording against a complete or
    pending op would either be after-the-fact theatre or premature.

    Resolve Conditional Finding (UX spec v1.5.1 §5.5.5) is a narrow,
    explicit exception: a completed op may accept a superseding
    inspection via the resolve-mode branch below.  That path uses
    `_require_resolve_mode_op_status` — not this helper — so the
    normal-inspection rule here stays untouched.
    """
    if op.status != OpStatus.in_progress:
        raise HTTPException(
            status_code=422,
            detail=(
                "Inspections and sign-offs may only be recorded while the "
                "operation is in_progress"
            ),
        )


def _require_resolve_mode_op_status(op: Operation) -> None:
    """Resolve Conditional Finding is valid on an in_progress or complete op.

    Spec §5.5.5: the queue surfaces conditional rows whose operation
    is already complete (post-completion resolution is the whole
    point of the flow), but a conditional finding on an op that was
    never completed is also resolvable while the op is still
    in_progress.  Any other status — pending, hold, rework — means
    the resolution would be recorded against state the spec does
    not sanction.
    """
    if op.status not in (OpStatus.in_progress, OpStatus.complete):
        raise HTTPException(
            status_code=422,
            detail=(
                "Resolve Conditional Finding requires the operation to be "
                "in_progress or complete"
            ),
        )


async def _labor_user_ids(
    session: AsyncSession, op_id: _uuid.UUID,
) -> list[_uuid.UUID]:
    """Load distinct user_ids that have labor entries on this operation.

    SOD-001 considers any labor on the operation regardless of cycle —
    a single LaborEntry can span a rework boundary, so cycle-scoping
    at this layer would miss legitimate SoD matches.
    """
    result = await session.execute(
        select(LaborEntry.user_id).where(LaborEntry.operation_id == op_id)
    )
    return [row[0] for row in result.all()]


async def _signoffs_for_op(
    session: AsyncSession, op_id: _uuid.UUID
) -> list[SignOffEvent]:
    """Load all sign-off events for an operation (across cycles).

    Cycle filtering is done by the rule evaluator so the rule remains
    the single source of truth for "what counts in the current cycle."
    """
    result = await session.execute(
        select(SignOffEvent).where(SignOffEvent.operation_id == op_id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


async def _create_reject_ncr(
    session: AsyncSession,
    op: Operation,
    inspection: InspectionEvent,
    user_id: _uuid.UUID,
) -> NonconformanceReport:
    """Atomically create an `open` NCR for a reject inspection result.

    Spec §5.5.4 / §5.5.5: a reject inspection creates an NCR "as
    usual" in the same transaction as the inspection event, so the
    floor never sees a window where a rejected operation has no
    corresponding nonconformance record.  Both normal-mode and
    resolve-mode reject paths funnel through here — the spec
    phrasing is the same for both.

    Delegates to ncr_service.create_ncr so the NCR numbering rule
    (NCR-YYYY-NNNN), the open/pending initial state, and the NCR
    creation audit event stay centralized.  Local import avoids a
    circular import at module load; ncr_service does not import
    inspection_service so there is no runtime cycle.
    """
    from forge.schemas.ncr import NCRCreate
    from forge.services import ncr_service

    description_parts: list[str] = [
        f"Inspection reject on operation {op.id} "
        f"(execution_cycle={inspection.execution_cycle}).",
        f"Criteria: {inspection.criteria}",
    ]
    if inspection.notes:
        description_parts.append(f"Notes: {inspection.notes}")
    if inspection.supersedes_inspection_id is not None:
        description_parts.append(
            f"Resolves conditional inspection "
            f"{inspection.supersedes_inspection_id}."
        )
    description = "\n".join(description_parts)[:4000]

    return await ncr_service.create_ncr(
        session,
        NCRCreate(
            work_order_id=op.work_order_id,
            operation_id=op.id,
            description=description,
            mrb_required=False,
        ),
        user_id=user_id,
        # Scope already verified by _get_op above; passing None
        # skips a redundant WO-scope check inside create_ncr.
        program_codes=None,
    )


async def _validate_resolve_mode(
    session: AsyncSession,
    op: Operation,
    data: InspectionCreate,
) -> InspectionEvent:
    """Enforce Resolve Conditional Finding preconditions (UX spec §5.5.5).

    Returns the referenced conditional inspection on success so the
    caller can build the audit snapshot without a second lookup.

    Invariants (in order — each failure path has a distinct 422):
      1. op.status ∈ {in_progress, complete} — enforced above, repeated
         here only as the caller's pre-check.
      2. result ∈ {accept, reject}.  `conditional` is a fresh finding
         verb, not a resolution verb — allowing it here would let the
         queue treat a "re-opened" conditional as resolved.
      3. Referenced inspection exists.
      4. Referenced inspection is on the SAME operation (no cross-op
         supersession — prevents silent leak of another op's state
         into this op's resolution chain).
      5. Referenced inspection's result is `conditional` — only
         conditional findings have resolution semantics.
      6. Single-resolution truth: the referenced inspection must not
         already be superseded by another event.  Allowing parallel
         supersessions would let two inspectors each "resolve" the
         same conditional with opposing results, and the queue
         derivation would have no principled way to pick one.  The
         spec §5.5.5 wording ("the conditional finding is resolved")
         treats resolution as a terminal event.
    """
    if data.result == InspectionResult.conditional:
        raise HTTPException(
            status_code=422,
            detail=(
                "Resolve Conditional Finding requires result accept or "
                "reject; `conditional` is invalid in resolve mode"
            ),
        )
    assert data.supersedes_inspection_id is not None  # caller-guaranteed
    referenced = await session.get(
        InspectionEvent, data.supersedes_inspection_id
    )
    if referenced is None:
        raise HTTPException(
            status_code=422,
            detail="Referenced inspection not found",
        )
    if referenced.operation_id != op.id:
        raise HTTPException(
            status_code=422,
            detail=(
                "Referenced inspection belongs to a different operation"
            ),
        )
    if referenced.result != InspectionResult.conditional:
        raise HTTPException(
            status_code=422,
            detail=(
                "Referenced inspection is not a conditional finding"
            ),
        )

    # Single-resolution check: reject if another event already names
    # this one as its supersedes target.
    existing = await session.execute(
        select(InspectionEvent.id).where(
            InspectionEvent.supersedes_inspection_id == referenced.id,
        ),
    )
    if existing.first() is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Referenced conditional inspection has already been "
                "resolved"
            ),
        )
    return referenced


async def record_inspection(
    session: AsyncSession,
    op_id: _uuid.UUID,
    data: InspectionCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> InspectionEvent:
    """Record an inspection event against an operation.

    Two modes, gated by `data.supersedes_inspection_id`:

    Normal mode (supersedes_inspection_id is None):
      State leaving: in_progress.  State entering: in_progress.
      Invariants:
        1. Op must be in_progress (recording window is the active cycle).
        2. SOD-001: inspector must not have labor entries on this op.
        3. execution_cycle is taken server-side from op.execution_cycle.

    Resolve Conditional Finding mode (supersedes_inspection_id set)
    per UX spec v1.5.1 §5.5.5 / R5-R2-ISSUE-006:
      State leaving: in_progress OR complete.  State entering:
      unchanged (the resolution does not mutate op.status in this
      slice — Reopen/Reactivate remain provisional per §5.3).
      Invariants:
        1. Op is in_progress OR complete.
        2. Result is accept or reject (conditional is invalid here).
        3. Referenced inspection exists, is on the same op, has
           result=conditional, and is not already superseded.
        4. SOD-001 still applies: the inspector resolving the
           conditional must not have labor on this op.
        5. execution_cycle is again taken server-side from
           op.execution_cycle — for a completed op this is the cycle
           the conditional was recorded against, preserving the
           "satisfies the hold in the relevant cycle" invariant.

    Reject side-effect (both modes): result=reject atomically
    creates an `open` NCR referencing this operation.  Spec §5.5.4 /
    §5.5.5 require the NCR to exist by the time the transaction
    commits so the floor never sees a rejected operation without a
    nonconformance record.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)

    resolve_mode = data.supersedes_inspection_id is not None
    if resolve_mode:
        _require_resolve_mode_op_status(op)
        await _validate_resolve_mode(session, op, data)
    else:
        _require_op_in_progress(op)

    labor_users = await _labor_user_ids(session, op_id)
    sod_violation = check_sod_001_self_inspection(
        inspector_id=user_id, labor_user_ids=labor_users
    )
    if sod_violation is not None:
        raise _violation_response(sod_violation)

    # Hydrate evidence refs from the registry (build spec v1.4.2
    # Decision 1).  The parent program is the op's WO program —
    # evidence must belong to the same program as the inspected
    # entity.  hydrate_refs_for_parent raises 422 with distinct
    # codes on any format / scope / program / confirmation failure
    # so the client can surface a targeted error.
    from forge.services.evidence_service import hydrate_refs_for_parent
    from forge.services.work_order_service import get_work_order
    wo = await get_work_order(
        session, op.work_order_id, program_codes=program_codes,
    )
    hydrated_refs = await hydrate_refs_for_parent(
        session=session,
        submitted=list(data.evidence_refs),
        expected_scope=EvidenceScope.inspection_evidence,
        expected_program_id=wo.program_id,
    )

    now = datetime.now(UTC)
    event = InspectionEvent(
        operation_id=op_id,
        inspector_id=user_id,
        execution_cycle=op.execution_cycle,
        criteria=data.criteria,
        result=data.result,
        measurements=data.measurements,
        evidence_refs=hydrated_refs,
        inspected_at=now,
        notes=data.notes,
        supersedes_inspection_id=data.supersedes_inspection_id,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(event)
    await session.flush()

    audit_after: dict[str, object] = {
        "operation_id": str(op_id),
        "execution_cycle": op.execution_cycle,
        "result": data.result.value,
    }
    if resolve_mode:
        audit_after["supersedes_inspection_id"] = str(
            data.supersedes_inspection_id
        )

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role="inspector",
        action_type="inspection.record",
        entity_type="inspection_event",
        entity_id=event.id,
        after_state=audit_after,
    )

    # Reject → atomic NCR creation (spec §5.5.4 / §5.5.5).  The NCR
    # and its own audit event are appended in the same transaction;
    # if this raises, the outer commit rolls back the inspection
    # event too so we never ship a reject without a matching NCR.
    if data.result == InspectionResult.reject:
        await _create_reject_ncr(session, op, event, user_id)

    # SSE publish (UX spec v1.5.1 §6.5.2 hold_point_changed).  Re-
    # derive hold_point_status from the current op + the event we
    # just appended so the payload reflects the post-commit truth.
    # _derive_hold_point_status walks op.inspection_events; ensure
    # the collection contains the new row before deriving.  Session
    # refresh is cheaper than a second SELECT because the identity
    # map already has the op + inspection rows.
    await session.refresh(op, attribute_names=["inspection_events"])
    new_hold_status = _derive_hold_point_status(op)
    inspection_id_str = str(event.id)
    # Defer to after the route commits.  Subscribers must never
    # observe state that is still rolled-backable.
    queue_publish(
        session,
        lambda: OPERATION_BUS.publish(
            op_id,
            "hold_point_changed",
            {
                "operation_id": str(op_id),
                "hold_point_status": new_hold_status.value,
                "inspection_id": inspection_id_str,
            },
        ),
    )

    return event


# ---------------------------------------------------------------------------
# Sign-off
# ---------------------------------------------------------------------------


async def record_sign_off(
    session: AsyncSession,
    op_id: _uuid.UUID,
    data: SignOffCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
    *,
    auth_context: AuthContext | None = None,
) -> SignOffEvent:
    """Record a role-based sign-off against an in-progress operation.

    State leaving: in_progress.  State entering: in_progress.
    Invariants (in order):
      1. Op must be in_progress.
      2. GC-3 freshness gate (only if safety_critical AND auth_context
         is provided): caller's auth_time must be within
         ``MAX_AUTH_AGE_SECONDS``.  Stale auth raises
         ``StaleAuthError`` (401 REAUTH_REQUIRED) BEFORE any rows or
         audit records are written, so the same Idempotency-Key retry
         after fresh login lands on a clean slate (UX spec §11.3).
      3. SOD-005 (only if safety_critical): the new (user_id, role) pair
         cannot reuse a user_id already present on a *different* role
         in the current execution_cycle.  Re-signing the same role is
         rejected by the unique DB constraint, not by SoD.
      4. execution_cycle is taken server-side from op.execution_cycle.

    auth_context is keyword-only and optional so callers that already
    pre-validated freshness (or operate outside the HTTP layer, like
    background workers) do not need to construct one.  In Phase 1 only
    the HTTP route layer calls this, and it always passes the live
    AuthContext.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)
    _require_op_in_progress(op)

    # GC-3: freshness gate fires AFTER scope/state checks (so program-
    # mismatch and op-state errors continue to take precedence) but
    # BEFORE any insert / audit-write side effect.  The same-key
    # Idempotency retry path depends on this ordering — a partial
    # commit before REAUTH_REQUIRED would let the cached idempotent
    # response permanently shadow the user's intent.
    if op.safety_critical and auth_context is not None:
        from forge.api.auth_freshness import (
            AuthFreshnessAction,
            require_fresh_auth,
        )

        await require_fresh_auth(
            auth_context, session, AuthFreshnessAction.sign_off
        )

    existing = await _signoffs_for_op(session, op_id)
    snapshots = [
        SoDSignOff(
            user_id=s.user_id, role=s.role, execution_cycle=s.execution_cycle
        )
        for s in existing
    ]
    sod_violation = check_sod_005_dual_signoff(
        safety_critical=op.safety_critical,
        current_cycle=op.execution_cycle,
        sign_offs=snapshots,
        new_user_id=user_id,
        new_role=data.role,
    )
    if sod_violation is not None:
        raise _violation_response(sod_violation)

    now = datetime.now(UTC)
    event = SignOffEvent(
        operation_id=op_id,
        user_id=user_id,
        role=data.role,
        execution_cycle=op.execution_cycle,
        action=data.action,
        signed_at=now,
        notes=data.notes,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(event)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=data.role,
        action_type="sign_off.record",
        entity_type="sign_off_event",
        entity_id=event.id,
        after_state={
            "operation_id": str(op_id),
            "execution_cycle": op.execution_cycle,
            "role": data.role,
            "action": data.action.value,
        },
    )
    return event


# ---------------------------------------------------------------------------
# Inspection Queue (UX spec v1.5.1 §5.5.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InspectionQueueFilters:
    """Query filters for GET /api/inspections (UX spec §5.5.5).

    Mirrors the URL query-param surface so the route layer can stay thin
    and service tests can exercise the full filter matrix without
    involving HTTP.  Every field is optional; None means "no restriction."
    """

    state: InspectionQueueState | None = None
    operation_id: _uuid.UUID | None = None
    safety_critical: bool | None = None


@dataclass(frozen=True)
class _QueueRow:
    """Intermediate queue row hydrated before pagination / filtering.

    Kept internal to the service: the route consumes serialized dicts
    via the InspectionQueueRowOut schema, not this dataclass.  Carrying
    the derived state + overdue fields here means the final filter /
    sort pass is a single traversal with no re-derivation.
    """

    operation: Operation
    work_order: WorkOrder
    program: Program
    state: InspectionQueueState
    hold_point_status: HoldPointStatus
    is_overdue: bool
    overdue_since: datetime | None
    conditional_inspection_id: _uuid.UUID | None
    inspected_at: datetime | None


def _latest_current_cycle_inspection(op: Operation) -> InspectionEvent | None:
    """Return the latest inspection event in the op's current execution cycle.

    Why this helper lives alongside the queue code (not as an
    Operation method): queue derivation treats "what satisfies the
    hold NOW" as a read-model concern, separate from the model's
    storage responsibilities.  Keeping it here also keeps COMP-001
    (forge.rules.compliance_checks) the sole owner of the
    completion-gate variant — the two live in different layers on
    purpose and must not cross-depend.

    Resolve Conditional Finding (UX spec §5.5.5) is handled naturally
    here: a superseding accept/reject is a later event in the same
    cycle, so max(inspected_at) picks it up and the queue state
    flips from `conditional` to `completed` (accept) or disappears
    from the inspector queue entirely (reject — handled on the
    NCR list).  No explicit "is-superseded" filter is needed.
    """
    cycle = op.execution_cycle
    cycle_events = [i for i in op.inspection_events if i.execution_cycle == cycle]
    if not cycle_events:
        return None
    latest: InspectionEvent = max(cycle_events, key=lambda i: i.inspected_at)
    return latest


def _derive_queue_state(
    hold_point_status: HoldPointStatus, is_overdue: bool,
) -> InspectionQueueState | None:
    """Map hold-point status + overdue flag to the §5.5.5 queue bucket.

    Returns None if the operation has no queue-relevant state (either
    inspection_hold is off, or the hold is BLOCKED — a reject that
    generated an NCR; the queue row for that lives on the NCR list,
    not here).  Overdue dominates pending/conditional per spec §5.1 —
    "Overdue Inspections" is a distinct dashboard tile, and the queue
    filter state=overdue must return these rows exclusively.
    """
    if hold_point_status == HoldPointStatus.none:
        return None
    if hold_point_status == HoldPointStatus.blocked:
        # Reject path — handled on the NCR list, not the inspection queue.
        return None
    if is_overdue:
        return InspectionQueueState.overdue
    if hold_point_status == HoldPointStatus.pending:
        return InspectionQueueState.pending
    if hold_point_status == HoldPointStatus.conditionally_satisfied:
        return InspectionQueueState.conditional
    # HoldPointStatus.satisfied — current-cycle accept; "completed" bucket.
    return InspectionQueueState.completed


def _compute_overdue(
    op: Operation, program: Program, hold_point_status: HoldPointStatus,
    now: datetime,
) -> tuple[bool, datetime | None]:
    """Derive (is_overdue, overdue_since) per UX spec v1.5.1 §5.1.

    An inspection is overdue when BOTH:
      1. The op is in_progress with an unsatisfied hold — that is,
         hold_point_status ∈ {pending, conditionally_satisfied}.
      2. The op has been in_progress (in its current execution_cycle)
         for longer than the program's overdue_threshold_hours.

    The clock is `operation.active_since`, which resets on every entry
    into in_progress (first start, hold resume, rework restart).  Using
    started_at instead would inherit the original-cycle start after
    rework and re-surface the op as "overdue" the instant it enters its
    second cycle — the exact bug §5.1's wording avoids.

    overdue_since is the moment the threshold was crossed
    (active_since + threshold), stable across polls so the client can
    render "X hours overdue."

    Backfill edge case: ops that existed before migration
    c4a7d2e91b38 had active_since copied from started_at, so the
    first-cycle overdue clock matches.  Reworked-before-migration
    ops carry the original-start bias until the next rework →
    in_progress transition resets active_since to now().  Documented
    in the migration docstring.
    """
    if op.status != OpStatus.in_progress:
        return False, None
    if hold_point_status not in (
        HoldPointStatus.pending, HoldPointStatus.conditionally_satisfied,
    ):
        return False, None
    if op.active_since is None:
        return False, None
    threshold = timedelta(hours=program.overdue_threshold_hours)
    overdue_at = op.active_since + threshold
    if now < overdue_at:
        return False, None
    return True, overdue_at


def _build_queue_row(
    op: Operation, wo: WorkOrder, program: Program, now: datetime,
) -> _QueueRow | None:
    """Hydrate a single queue row, or return None if it has no state.

    Combines the three derivations — hold_point_status (reused from
    operation_service so the vocabulary matches §5.4.3), overdue
    clock (§5.1), and queue bucket — into a single _QueueRow.  Ops
    without an inspection hold, or in the BLOCKED reject-path, are
    silently filtered so the caller does not carry them through
    pagination.
    """
    hold_status = _derive_hold_point_status(op)
    is_overdue, overdue_since = _compute_overdue(op, program, hold_status, now)
    state = _derive_queue_state(hold_status, is_overdue)
    if state is None:
        return None

    latest = _latest_current_cycle_inspection(op)
    conditional_id: _uuid.UUID | None = None
    if state == InspectionQueueState.conditional and latest is not None:
        # §5.5.5 / R5-R2-ISSUE-006: the queue row must point at the
        # specific conditional inspection so the frontend's
        # /ops/:opId/inspect?resolves=<inspectionId> link is valid.
        # Latest current-cycle inspection drives hold_point_status, so
        # "the conditional" is unambiguous here.
        conditional_id = latest.id

    return _QueueRow(
        operation=op, work_order=wo, program=program,
        state=state, hold_point_status=hold_status,
        is_overdue=is_overdue, overdue_since=overdue_since,
        conditional_inspection_id=conditional_id,
        inspected_at=latest.inspected_at if latest is not None else None,
    )


def _apply_base_filters(
    stmt: Select[tuple[Operation]], filters: InspectionQueueFilters,
    program_codes: list[str] | None,
) -> Select[tuple[Operation]]:
    """Narrow the candidate op set via DB predicates that are cheap and exact.

    Filters that are expressible without re-running the hold-derivation
    logic go here; the state-bucket filter is applied in Python
    post-hydration because deriving it requires walking inspection
    events (eager-loaded via selectinload).

    Program scope: joined via WorkOrder→Program on the code column, to
    match the auth-claim shape the rest of the API uses today (build
    spec §5.0.3 and pending auth-claim UUID migration).
    """
    stmt = stmt.join(WorkOrder, Operation.work_order_id == WorkOrder.id)
    stmt = stmt.join(Program, WorkOrder.program_id == Program.id)
    # Base: only operations that carry an inspection hold can have a
    # queue row; everything else is filtered at the DB to keep the
    # hydration path tight.
    stmt = stmt.where(Operation.inspection_hold.is_(True))

    if program_codes is not None and "*" not in program_codes:
        stmt = stmt.where(Program.code.in_(program_codes))

    if filters.operation_id is not None:
        stmt = stmt.where(Operation.id == filters.operation_id)

    if filters.safety_critical is not None:
        stmt = stmt.where(Operation.safety_critical.is_(filters.safety_critical))

    return stmt


def _sort_rows(rows: list[_QueueRow]) -> list[_QueueRow]:
    """Apply the §5.5.5 default sort: oldest pending first, then by
    safety-critical flag, then by overdue_since.

    Interpretation:
      1. Primary: started_at ASC (None treated as epoch so
         never-started ops sort first — a real edge case for
         inspection_hold ops that somehow have no start time).
      2. Secondary: safety_critical DESC (True before False).
      3. Tertiary: overdue_since ASC (earliest overdue first).

    A deterministic fallback on operation_id keeps pagination stable
    when all three keys tie (avoids row shuffling between pages).
    """
    epoch = datetime.min.replace(tzinfo=UTC)
    return sorted(
        rows,
        key=lambda r: (
            r.operation.started_at or epoch,
            not r.operation.safety_critical,
            r.overdue_since or epoch,
            str(r.operation.id),
        ),
    )


def _serialize_queue_row(row: _QueueRow) -> dict[str, object]:
    """Project a _QueueRow into the InspectionQueueRowOut shape.

    Explicit projection rather than model_validate(row) so the wire
    field names (work_order_number, sequence_number) stay decoupled
    from the internal attribute names (wo_number, sequence) — the
    same naming asymmetry documented in operation_service's
    predecessor summaries.
    """
    op = row.operation
    return {
        "operation_id": op.id,
        "work_order_id": op.work_order_id,
        "program_id": row.work_order.program_id,
        "work_order_number": row.work_order.wo_number,
        "sequence_number": op.sequence,
        "description": op.description,
        "operation_status": op.status,
        "hold_point_status": row.hold_point_status,
        "state": row.state,
        "safety_critical": op.safety_critical,
        "is_overdue": row.is_overdue,
        "overdue_since": row.overdue_since,
        "conditional_inspection_id": row.conditional_inspection_id,
        "inspected_at": row.inspected_at,
        "updated_at": op.updated_at,
    }


async def list_inspection_queue(
    session: AsyncSession,
    *,
    filters: InspectionQueueFilters,
    program_codes: list[str] | None,
    page: int = 1,
    page_size: int = 50,
    now: datetime | None = None,
) -> tuple[list[dict[str, object]], int]:
    """Paginate the Inspection Queue read-model (UX spec §5.5.5).

    Contract:
      - program scope enforced via WO→Program join on code (spec
        §5.0.3).  "*" in claims bypasses for local-dev parity.
      - candidate set = all operations with inspection_hold=True in
        the caller's program scope, optionally narrowed by
        operation_id / safety_critical.
      - each candidate is hydrated to a _QueueRow, which derives
        hold_point_status, overdue, and queue bucket.
      - state filter applied post-hydration (it depends on event
        history, not just op columns).
      - default sort + pagination applied last.

    Pagination tradeoff: total = count of post-filter rows, so the
    current implementation loads the full candidate set before
    paginating.  Acceptable at current volumes (inspection-hold ops
    per program, not all ops).  If this becomes hot, the state-bucket
    predicates become explicit EXISTS/NOT EXISTS subqueries and
    pagination drops to the DB.  Documenting the tradeoff here so the
    optimization is a drop-in replacement rather than a rewrite.
    """
    now = now or datetime.now(UTC)

    stmt: Select[tuple[Operation]] = select(Operation).options(
        selectinload(Operation.inspection_events),
    )
    stmt = _apply_base_filters(stmt, filters, program_codes)
    result = await session.execute(stmt)
    ops = list(result.scalars().all())

    # Hydrate rows by pulling WO + Program once per distinct key.
    # Lazy-loading through relationships inside an async session would
    # trip "greenlet_spawn" — fetch in bulk here.
    wo_ids = {op.work_order_id for op in ops}
    if not wo_ids:
        return [], 0
    wo_result = await session.execute(
        select(WorkOrder).where(WorkOrder.id.in_(wo_ids)),
    )
    wos = {wo.id: wo for wo in wo_result.scalars().all()}

    program_ids = {wo.program_id for wo in wos.values()}
    prog_result = await session.execute(
        select(Program).where(Program.id.in_(program_ids)),
    )
    programs = {p.id: p for p in prog_result.scalars().all()}

    rows: list[_QueueRow] = []
    for op in ops:
        wo = wos.get(op.work_order_id)
        if wo is None:
            continue  # defensive: FK guarantees this never fires
        program = programs.get(wo.program_id)
        if program is None:
            continue
        row = _build_queue_row(op, wo, program, now)
        if row is None:
            continue
        if filters.state is not None and row.state != filters.state:
            continue
        rows.append(row)

    rows = _sort_rows(rows)
    total = len(rows)
    offset = (page - 1) * page_size
    paged = rows[offset:offset + page_size]
    return [_serialize_queue_row(r) for r in paged], total
