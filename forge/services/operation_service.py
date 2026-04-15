#!/usr/bin/env python3
"""
Module:   Operation Service
Purpose:  Business logic for operation authoring, execution, labor capture,
          and the enriched detail read surface (UX spec v1.5.1 §5.4.8).

BoundState AI 2026
Version: 0.2.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Operation authoring is restricted to draft WOs.  Execution transitions
  follow spec §4.3.  Rework increments execution_cycle so prior
  inspection/sign-off events remain for audit but no longer satisfy
  hold enforcement.  Labor entries are created by op start and can be
  independently added/updated.

  The read-detail helpers near the top of the module (5B-6) compose
  three independent derivations — server-side hold_point_status,
  sequence-based predecessor summaries, and a wire-stable empty
  stop_requests list — into the OperationDetailOut shape required by
  the engineer Operation Detail page (§5.4.11) and the SSE reconnect
  contract (§6.5.2).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import (
    ActorType,
    HoldPointStatus,
    InspectionResult,
    OpStatus,
    WOStatus,
)
from forge.models.labor import LaborEntry
from forge.models.operation import Operation
from forge.models.work_order import WorkOrder
from forge.rules.compliance_checks import (
    ComplianceViolation,
    InspectionSnapshot,
    SignOffSnapshot,
    check_comp_001_inspection_hold,
    check_comp_003_signoffs_complete,
)
from forge.rules.status_transitions import validate_op_transition
from forge.schemas.labor import LaborCreate, LaborUpdate
from forge.schemas.operation import OperationCreate, OperationUpdate
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_wo(
    session: AsyncSession, wo_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Load WO with program-scope enforcement.

    Delegates to work_order_service.get_work_order which handles
    the program-scope check (spec §5.0.3).
    """
    from forge.services.work_order_service import get_work_order

    return await get_work_order(session, wo_id, program_codes=program_codes)


async def _get_op(
    session: AsyncSession, op_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Load operation with program-scope enforcement via parent WO.

    The operation's program scope is determined by its parent work
    order's program (spec §5.0.3).
    """
    op = await session.get(Operation, op_id)
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    # Enforce scope via parent WO
    if program_codes is not None:
        await _get_wo(session, op.work_order_id, program_codes=program_codes)
    return op


def _require_draft_wo(wo: WorkOrder) -> None:
    """Enforce that the WO is in draft status for authoring operations."""
    if wo.status != WOStatus.draft:
        raise HTTPException(
            status_code=422,
            detail="Operations can only be modified on draft work orders",
        )


# ---------------------------------------------------------------------------
# Read-detail helpers — UX spec v1.5.1 §5.4.8 (enriched OperationDetailOut)
# ---------------------------------------------------------------------------


def _derive_hold_point_status(op: Operation) -> HoldPointStatus:
    """Derive server-side hold_point_status per UX spec v1.5.1 §5.4.3.

    Mapping (latest InspectionResult in the current execution_cycle):
    - none: operation has no inspection hold (inspection_hold=False)
    - pending: hold exists, no inspection in current cycle
    - satisfied: latest result == accept
    - conditionally_satisfied: latest result == conditional
    - blocked: latest result == reject (NCR auto-created)

    Why filter by execution_cycle: rework increments the cycle so prior
    inspections remain in audit but no longer count for the current
    hold gate — mirrors COMP-001 (forge.rules.compliance_checks).

    Spec §5.4.3 explicitly forbids client-side derivation, so this
    function is the single source of truth for the UI's hold-point
    badge.

    KNOWN CONTRACT MISMATCH (carry-forward):
    Spec §5.3 (WO Complete preconditions) and §5.4.10 (Operation
    Complete tooltip) treat `conditionally_satisfied` as Complete-
    eligible alongside `satisfied`.  The current COMP-001 check in
    forge.rules.compliance_checks accepts ONLY `result == accept`,
    so a frontend that allows the [Complete Operation] click on a
    `conditionally_satisfied` op will receive a 422 from the server.
    Aligning COMP-001 to also accept `conditional` is its own slice
    item (tracked alongside the resolve-conditional flow in §5.5.5).
    Documenting here so the asymmetry is grep-able and so PR2 does
    not silently bake the contradiction in.
    """
    if not op.inspection_hold:
        return HoldPointStatus.none

    cycle_inspections = [
        i for i in op.inspection_events
        if i.execution_cycle == op.execution_cycle
    ]
    if not cycle_inspections:
        return HoldPointStatus.pending

    latest = max(cycle_inspections, key=lambda i: i.inspected_at)
    if latest.result == InspectionResult.accept:
        return HoldPointStatus.satisfied
    if latest.result == InspectionResult.conditional:
        return HoldPointStatus.conditionally_satisfied
    # InspectionResult.reject is the only remaining case; an NCR is
    # created in the same atomic transaction (spec §5.5.4).
    return HoldPointStatus.blocked


async def _load_predecessor_summaries(
    session: AsyncSession, op: Operation,
) -> list[Operation]:
    """Load predecessor operations for the §5.4.8 summaries field.

    The WO-level graph mode is the source-of-truth disambiguation:

      EXPLICIT MODE — the WO has ANY explicit edge in the
      operation_predecessors table.  In this mode every op's
      predecessors come from its own explicit set (possibly
      empty, which means this op is a root — the spec §5.4.6
      explicitly allows multiple roots).

      DEFAULT-SEQUENTIAL MODE — the WO has zero explicit edges.
      Every op's predecessor is the immediately-prior op by
      sequence, for authoring ergonomic on linear draft WOs.

    This per-WO determination matters because a per-op check
    ("does this op have explicit edges?") is ambiguous: an op
    with zero explicit predecessors inside an otherwise-
    explicit graph is a TRUE ROOT, not an implicitly-sequenced
    node.  Reading the mode at the WO level is the only honest
    resolution.

    Why sort siblings rather than `WHERE sequence == op.sequence - 1`
    in the default-sequential path: `Operation.sequence` is not
    guaranteed unique or gap-free — a literal `sequence - 1`
    lookup misses the predecessor in that case.  Sorting the
    actual sibling set is the only honest "previous sibling"
    semantics.
    """
    from forge.models.operation import operation_predecessors

    # One query: grab every edge where the successor belongs to
    # this WO.  If the set is non-empty the WO is in EXPLICIT mode
    # and only edges whose successor is THIS op are returned as
    # predecessors (an empty set = this op is a root).  If empty
    # the WO is in DEFAULT-SEQUENTIAL mode and we fall through.
    edge_result = await session.execute(
        select(
            operation_predecessors.c.operation_id,
            operation_predecessors.c.predecessor_id,
        ).where(
            operation_predecessors.c.operation_id.in_(
                select(Operation.id).where(
                    Operation.work_order_id == op.work_order_id,
                ),
            ),
        ),
    )
    all_edges = [(row[0], row[1]) for row in edge_result.all()]

    if all_edges:
        # EXPLICIT mode.
        pred_ids = [p for (succ, p) in all_edges if succ == op.id]
        if not pred_ids:
            return []
        pred_result = await session.execute(
            select(Operation).where(Operation.id.in_(pred_ids)),
        )
        return list(pred_result.scalars().all())

    # DEFAULT-SEQUENTIAL mode.
    result = await session.execute(
        select(Operation)
        .where(Operation.work_order_id == op.work_order_id)
        .order_by(Operation.sequence.asc(), Operation.id.asc())
    )
    siblings = list(result.scalars().all())
    # Locate this operation by id (sequence may collide); the
    # predecessor is the entry immediately before it in sorted order.
    for index, sibling in enumerate(siblings):
        if sibling.id == op.id:
            return [siblings[index - 1]] if index > 0 else []
    # Defensive — `op` should always be present in its own WO's
    # operation set.  Fall back to "no predecessor" rather than raise
    # so the read endpoint never 500s on a transient race.
    return []


def _serialize_predecessor_summary(pred: Operation) -> dict[str, object]:
    """Build the §5.4.8 summary dict for a single predecessor.

    `sequence_number` is the spec wire name; the model column is
    `sequence`.  Mapping happens here so the schema stays spec-named
    without requiring a database column rename.
    """
    return {
        "id": pred.id,
        "sequence_number": pred.sequence,
        "description": pred.description,
        "status": pred.status,
    }


async def get_operation_detail(
    session: AsyncSession,
    op_id: _uuid.UUID,
    *,
    program_codes: list[str] | None = None,
) -> dict[str, object]:
    """Load enriched operation detail per UX spec v1.5.1 §5.4.8.

    Returns a dict ready to feed `OperationDetailOut.model_validate`.
    Combines:
    - operation core fields
    - parent WO `program_id` (entity-route auto-switch — §3.2 / §6.7.4)
    - server-derived `hold_point_status` (§5.4.3)
    - sequence-based predecessor summaries (§5.4.8)
    - empty `stop_requests` (StopRequest model arrives in 5B-12;
      the field is wire-stable and always returned as `[]` until then)

    Authorization: `_get_wo` returns 404 on cross-program per UX spec
    §3.2 / §6.7.4 (existence-leak rule).  The caller route relies on
    that 404 for the entity-detail GET exception flow.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)
    wo = await _get_wo(session, op.work_order_id, program_codes=program_codes)

    predecessors = await _load_predecessor_summaries(session, op)
    return {
        "id": op.id,
        "work_order_id": op.work_order_id,
        "work_order_status": wo.status,
        "program_id": wo.program_id,
        "sequence_number": op.sequence,
        "description": op.description,
        "status": op.status,
        "execution_cycle": op.execution_cycle,
        "inspection_hold": op.inspection_hold,
        "safety_critical": op.safety_critical,
        "estimated_hours": op.estimated_hours,
        "started_at": op.started_at,
        "completed_at": op.completed_at,
        "hold_point_status": _derive_hold_point_status(op),
        "predecessors": [p.id for p in predecessors],
        "predecessor_summaries": [
            _serialize_predecessor_summary(p) for p in predecessors
        ],
        # Empty until 5B-12: StopRequest model + force-stop / stop-request
        # write endpoints land together.  Keeping the field present (not
        # absent) makes the OpenAPI contract stable across slices.
        "stop_requests": [],
        "version": op.version,
        "created_at": op.created_at,
        "updated_at": op.updated_at,
    }


# ---------------------------------------------------------------------------
# Operation authoring (draft WOs only)
# ---------------------------------------------------------------------------


async def create_operation(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    data: OperationCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Create operation on a draft WO."""
    wo = await _get_wo(session, wo_id, program_codes=program_codes)
    _require_draft_wo(wo)

    op = Operation(
        work_order_id=wo_id,
        sequence=data.sequence,
        description=data.description,
        tooling_required=data.tooling_required,
        inspection_hold=data.inspection_hold,
        safety_critical=data.safety_critical,
        sign_off_roles_required=data.sign_off_roles_required,
        estimated_hours=data.estimated_hours,
        constraint_type=data.constraint_type,
        constraint_date=data.constraint_date,
        status=OpStatus.pending,
        execution_cycle=1,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(op)
    await session.flush()
    return op


async def update_operation(
    session: AsyncSession,
    op_id: _uuid.UUID,
    data: OperationUpdate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Update operation fields (draft WO only)."""
    op = await _get_op(session, op_id, program_codes=program_codes)
    wo = await _get_wo(session, op.work_order_id, program_codes=program_codes)
    _require_draft_wo(wo)

    try:
        check_version(op, data.version, entity_type="Operation")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    for field in (
        "description", "sequence", "tooling_required", "inspection_hold",
        "safety_critical", "sign_off_roles_required", "estimated_hours",
        "constraint_type", "constraint_date",
    ):
        val = getattr(data, field)
        if val is not None:
            setattr(op, field, val)

    op.updated_by = user_id
    increment_version(op)
    await session.flush()
    return op


async def delete_operation(
    session: AsyncSession, op_id: _uuid.UUID, version: int,
    program_codes: list[str] | None = None,
) -> None:
    """Delete operation (draft WO only, version check)."""
    op = await _get_op(session, op_id, program_codes=program_codes)

    try:
        check_version(op, version, entity_type="Operation")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e
    wo = await _get_wo(session, op.work_order_id, program_codes=program_codes)
    _require_draft_wo(wo)
    await session.delete(op)
    await session.flush()


async def reorder_operations(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    operation_ids: list[_uuid.UUID],
    user_id: _uuid.UUID,
    version: int,
    program_codes: list[str] | None = None,
) -> list[Operation]:
    """Reorder operations by reassigning sequence numbers.

    Version check is on the parent WO since reordering is a logical
    mutation of the WO's operation structure.
    """
    wo = await _get_wo(session, wo_id, program_codes=program_codes)
    _require_draft_wo(wo)

    try:
        check_version(wo, version, entity_type="WorkOrder")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    result = await session.execute(
        select(Operation).where(Operation.work_order_id == wo_id)
    )
    ops = {op.id: op for op in result.scalars().all()}

    for seq, op_id in enumerate(operation_ids, start=1):
        op = ops.get(op_id)
        if op is None:
            raise HTTPException(
                status_code=422,
                detail=f"Operation {op_id} not found in work order",
            )
        op.sequence = seq
        op.updated_by = user_id

    await session.flush()
    return sorted(ops.values(), key=lambda o: o.sequence)


# ---------------------------------------------------------------------------
# Operation execution
# ---------------------------------------------------------------------------


async def _transition_op(
    session: AsyncSession,
    op_id: _uuid.UUID,
    target: OpStatus,
    version: int,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Apply an operation state transition with version check and audit.

    State leaving: op.status.  State entering: target.
    Invariant: transition must be in VALID_OP_TRANSITIONS.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)

    try:
        check_version(op, version, entity_type="Operation")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if not validate_op_transition(op.status, target):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid transition: {op.status.value} → {target.value}",
        )

    before = op.status.value
    op.status = target
    op.updated_by = user_id
    increment_version(op)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type=f"operation.{target.value}",
        entity_type="operation",
        entity_id=op.id,
        before_state={"status": before},
        after_state={"status": target.value},
    )

    # Spec §8.2: op status changes trigger CPM recalculation via the
    # outbox.  We publish against the parent work order so handler
    # routing lands on the right schedule.
    from forge.events.bus import emit_event
    from forge.models.enums import EventType

    await emit_event(
        session,
        event_type=EventType.operation_status_changed,
        aggregate_type="work_order",
        aggregate_id=str(op.work_order_id),
        payload={
            "operation_id": str(op.id),
            "before": before,
            "after": target.value,
        },
    )
    return op


async def start_operation(
    session: AsyncSession,
    op_id: _uuid.UUID,
    version: int,
    user_id: _uuid.UUID,
    role: str,
    shift: str,
    program_codes: list[str] | None = None,
) -> Operation:
    """Start operation: pending|rework|hold → in_progress, create labor entry.

    State leaving: pending, rework, or hold.  State entering: in_progress.
    Invariant: valid transition per spec §4.3.  Also transitions WO to
    in_progress if it is currently released (first op start drives WO state).

    If resuming from rework, execution_cycle is incremented per spec §3.1:
    "execution_cycle is incremented on each rework → in_progress transition."
    """
    # Capture pre-transition status to detect rework → in_progress
    op = await _get_op(session, op_id, program_codes=program_codes)
    was_rework = op.status == OpStatus.rework

    op = await _transition_op(
        session, op_id, OpStatus.in_progress, version, user_id,
        program_codes=program_codes,
    )
    now = datetime.now(UTC)
    if not was_rework:
        op.started_at = now

    # Spec §3.1: increment execution_cycle on rework → in_progress
    if was_rework:
        op.execution_cycle += 1

    # UX spec v1.5.1 §5.1: active_since is the overdue-clock source and
    # MUST reset on every entry into in_progress (including rework and
    # hold resumes), unlike started_at which is set once.  Setting it
    # unconditionally here covers first start, rework restart, and hold
    # resume via this route.
    op.active_since = now

    # Create labor entry for the starting operator
    labor = LaborEntry(
        operation_id=op_id,
        user_id=user_id,
        role=role,
        started_at=now,
        hours=0.0,
        shift=shift,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(labor)

    # Auto-transition WO from released → in_progress on first op start.
    # State leaving: released.  State entering: in_progress.
    # Invariant: WO must be in released status (first op start drives
    # the WO into active execution).  This is implicit from the WO
    # lifecycle — released means "ready to execute."
    wo = await _get_wo(session, op.work_order_id, program_codes=program_codes)
    if wo.status == WOStatus.released:
        wo.status = WOStatus.in_progress
        wo.updated_by = user_id
        increment_version(wo)

        await create_audit_record(
            session,
            event_id=_uuid.uuid4(),
            actor_type=ActorType.human,
            user_id=user_id,
            user_role=None,
            action_type="work_order.in_progress",
            entity_type="work_order",
            entity_id=wo.id,
            before_state={"status": "released"},
            after_state={"status": "in_progress"},
        )

    await session.flush()
    return op


async def stop_operation(
    session: AsyncSession,
    op_id: _uuid.UUID,
    version: int,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Stop the active labor entry for the current user on this operation.

    Does not transition the operation status — just ends the labor entry.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)

    try:
        check_version(op, version, entity_type="Operation")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    # Find the user's open labor entry on this operation
    result = await session.execute(
        select(LaborEntry).where(
            LaborEntry.operation_id == op_id,
            LaborEntry.user_id == user_id,
            LaborEntry.stopped_at.is_(None),
        )
    )
    labor = result.scalar_one_or_none()
    if labor is None:
        raise HTTPException(
            status_code=422,
            detail="No active labor entry found for this user on this operation",
        )

    now = datetime.now(UTC)
    labor.stopped_at = now
    elapsed = (now - labor.started_at).total_seconds() / 3600.0
    labor.hours = round(elapsed, 2)
    labor.updated_by = user_id
    increment_version(labor)

    op.updated_by = user_id
    increment_version(op)
    await session.flush()
    return op


async def complete_operation(
    session: AsyncSession, op_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Complete: in_progress → complete.

    State leaving: in_progress.  State entering: complete.
    Invariants (in order):
      1. Op exists and caller has program scope.
      2. Version matches.
      3. Status transition is valid per spec §4.3.
      4. COMP-001: if inspection_hold, an accepted inspection from the
         current execution_cycle must exist.
      5. COMP-003: every required sign-off role has at least one event
         from the current execution_cycle, and (if safety_critical) the
         signers are distinct users (SOD-005 distinctness).

      The compliance gates run BEFORE the transition so a failed
      completion leaves no audit residue from a half-applied transition.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)
    await _enforce_completion_gates(session, op)

    op = await _transition_op(
        session, op_id, OpStatus.complete, version, user_id,
        program_codes=program_codes,
    )
    op.completed_at = datetime.now(UTC)
    await session.flush()
    return op


def _completion_violation(violation: ComplianceViolation) -> HTTPException:
    """Convert a compliance violation into the §5.15 422 RULE_VIOLATION."""
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


async def _enforce_completion_gates(
    session: AsyncSession, op: Operation
) -> None:
    """Run COMP-001 and COMP-003 against the live event set.

    Loads inspection and sign-off events explicitly (rather than
    relying on relationship cache) because the cycle filter must apply
    to every event on the op, not whatever happens to be loaded in the
    session.  Cycle filtering is then done inside the rule evaluator so
    "what counts in the current cycle" has exactly one definition.
    """
    from forge.models.inspection import InspectionEvent, SignOffEvent

    inspections_raw = await session.execute(
        select(InspectionEvent).where(InspectionEvent.operation_id == op.id)
    )
    signoffs_raw = await session.execute(
        select(SignOffEvent).where(SignOffEvent.operation_id == op.id)
    )

    inspection_snapshots = [
        InspectionSnapshot(
            inspector_id=i.inspector_id,
            execution_cycle=i.execution_cycle,
            result=i.result.value,
        )
        for i in inspections_raw.scalars().all()
    ]
    signoff_snapshots = [
        SignOffSnapshot(
            user_id=s.user_id,
            role=s.role,
            execution_cycle=s.execution_cycle,
        )
        for s in signoffs_raw.scalars().all()
    ]

    comp1 = check_comp_001_inspection_hold(
        inspection_hold=op.inspection_hold,
        current_cycle=op.execution_cycle,
        inspections=inspection_snapshots,
    )
    if comp1 is not None:
        raise _completion_violation(comp1)

    comp3 = check_comp_003_signoffs_complete(
        sign_off_roles_required=op.sign_off_roles_required,
        safety_critical=op.safety_critical,
        current_cycle=op.execution_cycle,
        sign_offs=signoff_snapshots,
    )
    if comp3 is not None:
        raise _completion_violation(comp3)


async def hold_operation(
    session: AsyncSession, op_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Hold: in_progress → hold.

    State leaving: in_progress.  State entering: hold.
    Invariant: valid transition per spec §4.3.
    """
    return await _transition_op(
        session, op_id, OpStatus.hold, version, user_id,
        program_codes=program_codes,
    )


async def rework_operation(
    session: AsyncSession, op_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> Operation:
    """Rework: in_progress → rework.

    State leaving: in_progress.  State entering: rework.
    Invariant: valid transition per spec §4.3.
    Does NOT increment execution_cycle here — that happens on the
    subsequent rework → in_progress transition (resume after rework).
    """
    return await _transition_op(
        session, op_id, OpStatus.rework, version, user_id,
        program_codes=program_codes,
    )


async def resume_operation(
    session: AsyncSession, op_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    role: str, shift: str,
) -> Operation:
    """Resume from hold or rework → in_progress.

    State leaving: hold or rework.  State entering: in_progress.
    Invariant: valid transition per spec §4.3.  If resuming from
    rework, execution_cycle is incremented per spec §3.1 before any
    new inspection/sign-off events are recorded against the operation.
    A new labor entry is created for the resuming operator.
    """
    op = await _get_op(session, op_id)
    was_rework = op.status == OpStatus.rework

    op = await _transition_op(session, op_id, OpStatus.in_progress, version, user_id)

    if was_rework:
        # Spec §3.1: "execution_cycle is incremented on each
        # rework → in_progress transition"
        op.execution_cycle += 1

    now = datetime.now(UTC)
    # Spec §5.1: reset active_since on every entry into in_progress so
    # the overdue clock measures the CURRENT cycle.  Mirror of the
    # start_operation path; both routes must stay in sync or an op
    # resumed via this endpoint would keep a stale active_since.
    op.active_since = now

    labor = LaborEntry(
        operation_id=op_id,
        user_id=user_id,
        role=role,
        started_at=now,
        hours=0.0,
        shift=shift,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(labor)
    await session.flush()
    return op


# ---------------------------------------------------------------------------
# Labor
# ---------------------------------------------------------------------------


async def create_labor_entry(
    session: AsyncSession,
    op_id: _uuid.UUID,
    data: LaborCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> LaborEntry:
    """Create a labor entry on an operation."""
    op = await _get_op(session, op_id, program_codes=program_codes)
    if op.status not in (OpStatus.in_progress, OpStatus.hold):
        raise HTTPException(
            status_code=422,
            detail="Labor can only be added to in-progress or held operations",
        )

    now = datetime.now(UTC)
    labor = LaborEntry(
        operation_id=op_id,
        user_id=user_id,
        role=data.role,
        started_at=now,
        hours=0.0,
        shift=data.shift,
        notes=data.notes,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(labor)
    await session.flush()
    return labor


async def update_labor_entry(
    session: AsyncSession,
    labor_id: _uuid.UUID,
    data: LaborUpdate,
    user_id: _uuid.UUID,
    can_update_any: bool,
    program_codes: list[str] | None = None,
) -> LaborEntry:
    """Update a labor entry (own or any, per permission).

    labor:update_own: can only update entries where user_id matches.
    labor:update_any: can update any entry.

    Program scope (spec §5.0.3): the labor entry's parent operation's
    work order must be in the caller's program_memberships.
    Enforcement via _get_op which walks to the parent WO.
    """
    labor = await session.get(LaborEntry, labor_id)
    if labor is None:
        raise HTTPException(status_code=404, detail="Labor entry not found")

    # Enforce program scope via parent operation's WO.
    # Raises 403 if caller is not in the WO's program.
    await _get_op(session, labor.operation_id, program_codes=program_codes)

    if not can_update_any and labor.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot update another user's labor entry",
        )

    try:
        check_version(labor, data.version, entity_type="LaborEntry")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if data.stopped_at is not None:
        labor.stopped_at = data.stopped_at
    if data.hours is not None:
        labor.hours = data.hours
    if data.shift is not None:
        labor.shift = data.shift
    if data.notes is not None:
        labor.notes = data.notes

    labor.updated_by = user_id
    increment_version(labor)
    await session.flush()
    return labor
