#!/usr/bin/env python3
"""
Module:   Work Order Service
Purpose:  Business logic for work order CRUD and state transitions.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Business logic lives in services, not routes (CLAUDE.md build
  philosophy).  State transitions enforce the spec §4.3 transition map
  via the rules engine.  Draft-only edits are enforced here so routes
  remain thin.  Audit records are created for critical transitions.
  WO number auto-generation uses WO-YYYY-NNNN format (spec §3.1).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from forge.api.auth import AuthContext
from forge.audit.logger import create_audit_record
from forge.config import Settings
from forge.models.enums import ActorType, OpStatus, WOStatus
from forge.models.work_order import WorkOrder
from forge.realtime import (
    OPERATION_BUS,
    publish_labor_activity,
    publish_wo_state_changed_to_ops,
    queue_publish,
    resolve_actor,
)
from forge.rules.status_transitions import validate_wo_transition
from forge.schemas.work_order import WorkOrderCreate, WorkOrderUpdate
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)


async def _publish_wo_state_changed(
    session: AsyncSession,
    wo: WorkOrder,
    *,
    previous_state: str,
    actor_id: _uuid.UUID,
    reason: str | None = None,
    force_stop_labor: bool = False,
    stopped_labor_entries: list[dict[str, _uuid.UUID]] | None = None,
) -> None:
    """Publish the §6.5.2 wo_state_changed SSE event.

    Fans the event out to every op channel on the WO so any
    Operation Execution subscriber sees the parent-WO transition.
    Hold-path callers pass reason + force_stop_labor + stopped
    entries so the client can render the §5.3 stop-work banner
    directly from the event; other transitions pass defaults and
    the payload shape stays stable.
    """
    actor = await resolve_actor(session, actor_id)
    stopped_entries = [
        {
            "entry_id": str(entry["entry_id"]),
            "operation_id": str(entry["operation_id"]),
            "user_id": str(entry["user_id"]),
        }
        for entry in (stopped_labor_entries or [])
    ]
    payload: dict[str, object] = {
        "work_order_id": str(wo.id),
        "state": wo.status.value,
        "previous_state": previous_state,
        "actor": {
            "user_id": actor["id"],
            "display_name": actor["display_name"],
        },
        "reason": reason,
        "force_stop_labor": force_stop_labor,
        "stopped_labor_entries": stopped_entries,
    }
    await publish_wo_state_changed_to_ops(session, wo.id, payload)


def _wo_to_conflict_dict(wo: WorkOrder) -> dict[str, object]:
    """Serialize a WorkOrder for the §6.3.2 409 `current_entity` field.

    Uses WorkOrderOut.model_validate so the payload shape matches the
    GET response exactly — the client's cached entity can be
    replaced directly from this dict without a reshape layer.
    """
    from forge.schemas.work_order import WorkOrderOut

    return WorkOrderOut.model_validate(wo).model_dump(mode="json")


async def _generate_wo_number(session: AsyncSession) -> str:
    """Generate the next WO number in WO-YYYY-NNNN format.

    Why count-based: simple and human-readable for manufacturing floor
    use.  Race condition risk is mitigated by the unique constraint on
    wo_number — a duplicate would raise IntegrityError, caught and
    retried by the caller.  A DB sequence would be more robust but
    adds complexity not yet justified at this traffic level.
    """
    year = datetime.now(UTC).year
    prefix = f"WO-{year}-"
    result = await session.execute(
        select(func.count()).where(WorkOrder.wo_number.startswith(prefix))
    )
    count = result.scalar() or 0
    return f"{prefix}{count + 1:04d}"


async def create_work_order(
    session: AsyncSession,
    data: WorkOrderCreate,
    user_id: _uuid.UUID,
) -> WorkOrder:
    """Create a new work order in draft status."""
    wo_number = await _generate_wo_number(session)
    wo = WorkOrder(
        wo_number=wo_number,
        program_id=data.program_id,
        engine_serial=data.engine_serial,
        module=data.module,
        build_phase=data.build_phase,
        status=WOStatus.draft,
        priority=data.priority,
        authored_by_id=user_id,
        bom_revision_ref=data.bom_revision_ref,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(wo)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="work_order.create",
        entity_type="work_order",
        entity_id=wo.id,
        after_state={"status": wo.status.value, "wo_number": wo.wo_number},
    )
    return wo


async def get_work_order(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Load a work order by ID, enforcing program scope.

    UX spec v1.5.1 §3.2 / §6.7.4: cross-program access returns **404,
    not 403** — surfacing 403 would confirm the entity exists in a
    program the caller cannot see and leak cross-program metadata.
    The shape is intentionally indistinguishable from "no such record."

    Wildcard "*" in the caller's claim bypasses the membership check
    (local-dev parity).  Build spec §5.0.3 keeps program-scope
    enforcement at the service layer.
    """
    wo = await session.get(WorkOrder, wo_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    if program_codes is not None and "*" not in program_codes:
        # Load the program relationship to compare against the caller's
        # claim values (today: program codes; eventually: program UUIDs
        # per the auth-claim-shape migration).
        await session.refresh(wo, ["program"])
        if wo.program.code not in program_codes:
            raise HTTPException(
                status_code=404, detail="Work order not found",
            )
    return wo


async def list_work_orders(
    session: AsyncSession,
    *,
    program_codes: list[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[WorkOrder], int]:
    """List work orders with pagination and program-scope filtering.

    Spec §5.0.3: all data-access endpoints automatically filter results
    to the user's program_memberships.  Wildcard "*" bypasses for local dev.
    """
    from forge.models.program import Program

    base_query = select(WorkOrder)
    count_query = select(func.count(WorkOrder.id))

    # Apply program scope filter unless wildcard
    if program_codes is not None and "*" not in program_codes:
        base_query = base_query.join(Program).where(
            Program.code.in_(program_codes)
        )
        count_query = count_query.join(Program).where(
            Program.code.in_(program_codes)
        )

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(
        base_query
        .order_by(WorkOrder.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list(result.scalars().all())
    return items, total


async def update_work_order(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    data: WorkOrderUpdate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Update a work order (draft only, version check).

    State leaving: draft.  State entering: draft (no transition).
    Invariant: WO must be in draft status.
    """
    wo = await get_work_order(session, wo_id, program_codes=program_codes)

    if wo.status != WOStatus.draft:
        raise HTTPException(
            status_code=422,
            detail="Only draft work orders can be edited",
        )

    # Route-layer resolve_version_assertion already guarantees int.
    assert data.version is not None, "route must resolve version before service"
    try:
        check_version(wo, data.version, entity_type="WorkOrder")
    except VersionConflictError as e:
        raise version_conflict_response(
            e, current_entity=_wo_to_conflict_dict(wo),
        ) from e

    if data.engine_serial is not None:
        wo.engine_serial = data.engine_serial
    if data.module is not None:
        wo.module = data.module
    if data.build_phase is not None:
        wo.build_phase = data.build_phase
    if data.priority is not None:
        wo.priority = data.priority
    if data.bom_revision_ref is not None:
        wo.bom_revision_ref = data.bom_revision_ref

    wo.updated_by = user_id
    increment_version(wo)
    await session.flush()
    return wo


async def _transition_wo(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    target: WOStatus,
    version: int,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
    *,
    action_type_override: str | None = None,
) -> WorkOrder:
    """Apply a state transition with version check and audit.

    State leaving: wo.status.  State entering: target.
    Invariant: transition must be in VALID_WO_TRANSITIONS map.

    `action_type_override` lets a caller emit a spec-named audit
    event when the default `work_order.<target_status>` would be
    the wrong name.  Concrete case: Recall targets the `draft`
    state but UX spec §6.6.1 names the audit event
    `work_order.recalled` (past tense), not `work_order.draft`.
    Defaulting to None preserves every other transition's existing
    audit name verbatim — this is an additive kwarg, not a rename.
    """
    wo = await get_work_order(session, wo_id, program_codes=program_codes)

    try:
        check_version(wo, version, entity_type="WorkOrder")
    except VersionConflictError as e:
        raise version_conflict_response(
            e, current_entity=_wo_to_conflict_dict(wo),
        ) from e

    if not validate_wo_transition(wo.status, target):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid transition: {wo.status.value} → {target.value}",
        )

    before_status = wo.status.value
    wo.status = target
    wo.updated_by = user_id
    increment_version(wo)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type=action_type_override or f"work_order.{target.value}",
        entity_type="work_order",
        entity_id=wo.id,
        before_state={"status": before_status},
        after_state={"status": target.value},
    )
    return wo


async def release_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Release: draft → released.

    State leaving: draft.  State entering: released.
    Invariants (in order):
      1. Caller has program scope (404 on cross-program).
      2. SOD-004: authored_by_id != user_id (spec §4.2, §5.1 line 885).
      3. DAG validation: the WO's explicit predecessor graph must
         pass the same checks that Fix Graph runs (UX spec §5.3
         Release precondition: "operation dependency graph passes
         DAG validation").  A WO with zero explicit edges passes
         trivially (empty DAG is a valid DAG) — the default-
         sequential read-fallback is unaffected.
      4. Status transition is valid per spec §4.3.

    Order matters: SOD-004 before graph validation so a self-release
    attempt gets the SOD error (actionable "assign a second
    engineer") rather than a graph error that reads as a graph bug.
    """
    # SOD-004: load WO first to check authored_by_id before transition
    wo = await get_work_order(session, wo_id, program_codes=program_codes)
    if wo.authored_by_id == user_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RULE_VIOLATION",
                "message": "SOD-004: author cannot release their own work order",
                "violations": [{
                    "rule_id": "SOD-004",
                    "rule_name": "Work order release independence",
                    "severity": "block",
                    "message": "The work order author cannot also release it",
                    "context": {},
                }],
            },
        )

    await _validate_release_graph(session, wo)

    previous_state = wo.status.value
    wo = await _transition_wo(
        session, wo_id, WOStatus.released, version, user_id,
        program_codes=program_codes,
    )
    wo.released_at = datetime.now(UTC)
    wo.released_by = user_id
    await session.flush()
    await _publish_wo_state_changed(
        session, wo, previous_state=previous_state, actor_id=user_id,
    )
    return wo


async def _validate_release_graph(
    session: AsyncSession, wo: WorkOrder,
) -> None:
    """Release-precondition DAG check (UX spec §5.3 / §5.4.6).

    Loads every op in the WO + the union of their explicit
    predecessor edges (queried directly from the association table,
    not via Operation.predecessors — a self-ref lazy-load under an
    async session is unsafe), then runs the shared validator.
    Raises 422 with the same WO-REL-101/102/103 codes Fix Graph
    uses — spec §5.4.6 says these codes apply "same as release-time
    validation," so this is the single source of truth.

    Also enforces the §5.3 "at least one operation defined"
    precondition: a WO with zero ops cannot release.  Prior to
    this guard, a race / silent setup failure that left the
    operations table empty would still pass release (empty DAG is
    trivially valid) — and downstream graph/labor flows would then
    fail in misleading places.  Raising a specific WO-REL-104
    rule violation here makes the setup failure visible at its
    origin.

    Fresh-draft WOs with one linear op chain have zero explicit
    edges; the validator returns an empty error list on that set
    and release proceeds.  The default-sequential authoring
    ergonomic is unaffected.
    """
    from forge.models.operation import Operation, operation_predecessors
    from forge.rules.graph_validation import validate_graph

    result = await session.execute(
        select(Operation.id).where(Operation.work_order_id == wo.id),
    )
    op_ids = [row[0] for row in result.all()]
    if not op_ids:
        # UX spec v1.5.1 §5.3 Release precondition: "At least one
        # operation defined".  WO-REL-104 keeps the error code
        # namespace aligned with the existing 101/102/103 DAG codes
        # so the frontend has a single branch for WO-REL-* on
        # release failure.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "GRAPH_VALIDATION_FAILED",
                "message": "Graph validation failed",
                "validation_errors": [{
                    "code": "WO-REL-104",
                    "detail": (
                        "Work order has no operations defined; "
                        "release requires at least one operation."
                    ),
                    "operation_ids": [],
                }],
            },
        )

    edge_result = await session.execute(
        select(
            operation_predecessors.c.operation_id,
            operation_predecessors.c.predecessor_id,
        ).where(operation_predecessors.c.operation_id.in_(op_ids)),
    )
    edges = [(row[0], row[1]) for row in edge_result.all()]

    errors = validate_graph(op_ids, edges)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "GRAPH_VALIDATION_FAILED",
                "message": "Graph validation failed",
                "validation_errors": [
                    {
                        "code": e.code,
                        "detail": e.detail,
                        "operation_ids": [str(oid) for oid in e.operation_ids],
                    }
                    for e in errors
                ],
            },
        )


async def recall_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Recall: released → draft (UX spec v1.5.1 §5.3 Recall transition).

    State leaving: released.  State entering: draft.
    Invariant: valid transition per VALID_WO_TRANSITIONS.
      - "WO is in released state (no operations have been started;
        if any had, WO would be in_progress)" — this precondition is
        structurally guaranteed by the state machine: the first op
        start atomically moves the WO to in_progress, so if we are
        still in released, no op has started.  No additional runtime
        check is required.
      - released_at / released_by are deliberately NOT cleared on
        recall — the spec calls Recall a pre-activation correction
        step, and wiping release history would erase the audit
        trail of "this WO was released once and then pulled back."
        A subsequent re-release increments those fields fresh; the
        prior values live on in audit records.

    Emits audit event `work_order.recalled` (UX spec §6.6.1) rather
    than the default `work_order.<target_status>` because spec
    §6.6.1 names the event in past tense.
    """
    wo_before = await get_work_order(
        session, wo_id, program_codes=program_codes,
    )
    previous_state = wo_before.status.value
    wo = await _transition_wo(
        session, wo_id, WOStatus.draft, version, user_id,
        program_codes=program_codes,
        action_type_override="work_order.recalled",
    )
    await _publish_wo_state_changed(
        session, wo, previous_state=previous_state, actor_id=user_id,
    )
    return wo


async def hold_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
    *,
    force_stop_labor: bool = False,
    reason: str | None = None,
) -> tuple[WorkOrder, list[dict[str, _uuid.UUID]]]:
    """Hold: released|in_progress → hold (UX spec v1.5.1 §5.3).

    State leaving: released or in_progress.  State entering: hold.
    Invariant: valid transition per spec §4.3.

    When `force_stop_labor=True`:
      - atomically stop every active labor entry on child operations
      - emit one `labor.stopped_by_hold` audit per stopped entry
      - expire any pending stop-requests on those entries (spec
        §5.4.9: hold-cascade stop is an OTHER mechanism, not a
        force-stop endpoint fulfillment)

    `reason` is audited but does not alter behavior — UX §5.3
    Hold Cascading binds it as future SSE payload data.

    Returns the updated WO plus a list of dicts carrying the entry_id
    / operation_id / user_id of every stopped entry (empty list when
    `force_stop_labor=False`).  The route layer wraps the dicts in
    StoppedLaborEntryOut for the wire response.
    """
    # Capture pre-transition state so the SSE payload's
    # previous_state is truthful — the transition helper mutates
    # wo.status in place.
    wo_before = await get_work_order(
        session, wo_id, program_codes=program_codes,
    )
    previous_state = wo_before.status.value

    # Transition the WO first.  _transition_wo runs the version
    # check + state-machine validation + audit record.  If the
    # cascade fails mid-way, we still want the WO to have
    # transitioned — a partial hold that stops nothing is safer
    # than a rollback where the frontend thinks the hold was
    # rejected.  Concretely this matters zero today because the
    # cascade is idempotent, but it keeps the contract clean.
    wo = await _transition_wo(
        session, wo_id, WOStatus.hold, version, user_id,
        program_codes=program_codes,
    )

    stopped: list[dict[str, _uuid.UUID]] = []
    if force_stop_labor:
        stopped = await _cascade_stop_active_labor(
            session, wo.id, user_id, reason=reason,
        )

    await _publish_wo_state_changed(
        session, wo,
        previous_state=previous_state,
        actor_id=user_id,
        reason=reason,
        force_stop_labor=force_stop_labor,
        stopped_labor_entries=stopped,
    )

    return wo, stopped


async def _cascade_stop_active_labor(
    session: AsyncSession, wo_id: _uuid.UUID, actor_id: _uuid.UUID,
    *, reason: str | None,
) -> list[dict[str, _uuid.UUID]]:
    """Stop every active labor entry on this WO's child ops.

    Helper split out so the hold path stays readable and so a future
    "stop-work panic button" endpoint can reuse the same cascade
    without a second implementation.  Uses the shared
    stop_labor_entry_and_resolve helper so pending stop-requests
    transition to `expired` (not `fulfilled` — this is a hold-
    cascade stop, not the force-stop endpoint).

    Emits `labor.stopped_by_hold` per stopped entry with the
    cascading actor's user_id and the hold reason.  Spec §5.3
    names this audit event explicitly.
    """
    from forge.audit.logger import create_audit_record
    from forge.models.enums import ActorType
    from forge.models.labor import LaborEntry
    from forge.models.operation import Operation
    from forge.services.stop_request_service import (
        StopFulfillmentMode,
        stop_labor_entry_and_resolve,
    )

    # Active labor entries on any child op of this WO.
    result = await session.execute(
        select(LaborEntry)
        .join(Operation, LaborEntry.operation_id == Operation.id)
        .where(
            Operation.work_order_id == wo_id,
            LaborEntry.stopped_at.is_(None),
        ),
    )
    entries = list(result.scalars().all())
    if not entries:
        return []

    now = datetime.now(UTC)
    stopped: list[dict[str, _uuid.UUID]] = []
    for labor in entries:
        summary = await stop_labor_entry_and_resolve(
            session, labor, actor_id,
            fulfillment_mode=StopFulfillmentMode.OTHER,
            stopped_at=now,
        )
        stopped.append({
            "entry_id": summary.labor_entry_id,
            "operation_id": summary.operation_id,
            "user_id": summary.user_id,
        })
        await create_audit_record(
            session,
            event_id=_uuid.uuid4(),
            actor_type=ActorType.human,
            user_id=actor_id,
            user_role=None,
            action_type="labor.stopped_by_hold",
            entity_type="labor_entry",
            entity_id=labor.id,
            before_state={"stopped_at": None},
            after_state={
                "stopped_at": now.isoformat(),
                "target_user_id": str(labor.user_id),
                "reason": reason,
            },
        )

        # SSE publish: each cascade-stopped entry surfaces as a
        # labor_activity event on the affected op channel so each
        # operator sees their own timer end immediately (§6.5.2).
        # The parent WO hold also publishes its own
        # wo_state_changed carrying the full stop-work payload —
        # labor_activity here is the op-centric counterpart.
        await publish_labor_activity(
            session, labor.operation_id, actor_id,
            action="stopped_by_hold", entry_id=labor.id,
        )

    return stopped


async def resume_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Resume: hold → in_progress.

    State leaving: hold.  State entering: in_progress.
    Invariant: valid transition per spec §4.3.
    """
    wo_before = await get_work_order(
        session, wo_id, program_codes=program_codes,
    )
    previous_state = wo_before.status.value
    wo = await _transition_wo(
        session, wo_id, WOStatus.in_progress, version, user_id,
        program_codes=program_codes,
    )
    await _publish_wo_state_changed(
        session, wo, previous_state=previous_state, actor_id=user_id,
    )
    return wo


async def complete_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Complete: in_progress → complete.  All ops must be complete."""
    wo = await get_work_order(session, wo_id, program_codes=program_codes)
    incomplete = [op for op in wo.operations if op.status != OpStatus.complete]
    if incomplete:
        raise HTTPException(
            status_code=422,
            detail=f"{len(incomplete)} operation(s) are not complete",
        )
    previous_state = wo.status.value
    wo = await _transition_wo(
        session, wo_id, WOStatus.complete, version, user_id,
        program_codes=program_codes,
    )
    await _publish_wo_state_changed(
        session, wo, previous_state=previous_state, actor_id=user_id,
    )
    return wo


async def compute_closure_blockers(
    session: AsyncSession, wo: WorkOrder,
) -> list[dict[str, Any]]:
    """Server-derived list of reasons a WO close cannot proceed (GC-4).

    Single source of truth for both the WorkOrderDetailOut read
    surface (so the frontend can disable the close button + render a
    precise tooltip without a click probe) AND the close service
    write path (so a click produces the same machine-readable code
    the tooltip showed).

    Each blocker is `{code, message, entity_id?}` — codes are the
    same identifiers `close_work_order` raises on click:
      - WO_CLOSE_NCRS_NOT_CLOSED     (one entry per offending NCR)
      - WO_CLOSE_UNRESOLVED_CONDITIONALS (one entry per inspection)

    The "WO is in `complete`" precondition is enforced by the
    transition-rule layer (round-4 fix dropped `hold → closed`), so
    this helper does NOT duplicate that check — it focuses on
    closure-readiness invariants the UI needs to surface in advance.
    """
    from forge.models.inspection import InspectionEvent
    from forge.models.ncr import NonconformanceReport
    from forge.models.operation import Operation

    blockers: list[dict[str, Any]] = []

    # 1) NCRs-not-closed: every NCR linked to this WO must be `closed`
    #    (NCRStatus.closed).  `voided` is also an acceptable terminal
    #    — voided NCRs were rolled back administratively and do not
    #    block close per §3.1 lifecycle.
    open_ncr_stmt = (
        select(
            NonconformanceReport.id,
            NonconformanceReport.ncr_number,
            NonconformanceReport.status,
        )
        .where(
            NonconformanceReport.work_order_id == wo.id,
            NonconformanceReport.status.notin_(["closed", "voided"]),
        )
        .order_by(NonconformanceReport.created_at.asc())
    )
    open_ncrs = (await session.execute(open_ncr_stmt)).all()
    for ncr_id, ncr_number, ncr_status in open_ncrs:
        status_value = (
            ncr_status.value if hasattr(ncr_status, "value") else ncr_status
        )
        blockers.append({
            "code": "WO_CLOSE_NCRS_NOT_CLOSED",
            "message": (
                f"NCR {ncr_number} is in `{status_value}` — "
                f"must be closed before WO close."
            ),
            "entity_id": ncr_id,
        })

    # 2) Unresolved conditional inspections: every inspection event
    #    on this WO's ops with result=conditional must have a later
    #    superseding event (i.e. another inspection_events row whose
    #    supersedes_inspection_id points at it).  An anti-join query
    #    surfaces the unresolved set in one round-trip.
    superseder = aliased(InspectionEvent)
    conditional_stmt = (
        select(
            InspectionEvent.id,
            InspectionEvent.operation_id,
            Operation.sequence,
            Operation.description,
        )
        .join(Operation, Operation.id == InspectionEvent.operation_id)
        .outerjoin(
            superseder,
            superseder.supersedes_inspection_id == InspectionEvent.id,
        )
        .where(
            Operation.work_order_id == wo.id,
            InspectionEvent.result == "conditional",
            superseder.id.is_(None),
        )
        .order_by(Operation.sequence.asc(), InspectionEvent.inspected_at.asc())
    )
    unresolved = (await session.execute(conditional_stmt)).all()
    for _insp_id, op_id, op_seq, op_desc in unresolved:
        blockers.append({
            "code": "WO_CLOSE_UNRESOLVED_CONDITIONALS",
            "message": (
                f"Op {op_seq} ({op_desc}) has an unresolved conditional "
                f"inspection — record an accept/reject inspection that "
                f"supersedes it before WO close."
            ),
            "entity_id": op_id,
        })

    return blockers


async def close_work_order(
    session: AsyncSession,
    wo_id: _uuid.UUID,
    version: int,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
    *,
    auth_context: AuthContext,
    closure_rationale: str | None = None,
    settings: Settings | None = None,
) -> WorkOrder:
    """Close: complete → closed.  GC-4 closure-as-attestation contract.

    State leaving: complete.  State entering: closed.
    Invariants (in order — every gate fires BEFORE any state mutation
    or audit row is written, so a rejected attempt leaves a clean
    slate for retry):
      1. `check_closure_enabled` → 403 CLOSURE_DISABLED if the
         feature flag is off (governance-gated by MSFC QA approval
         or formal QA waiver per UX spec §5.3).
      2. `require_fresh_auth(wo_close)` → 401 REAUTH_REQUIRED on
         stale auth (spec §11.3).
      3. `compute_closure_blockers` → 422 with the FIRST blocker's
         `code` as the top-level code and the full blocker list in
         the detail.  The same helper feeds the
         WorkOrderDetailOut.closure_blockers read surface so the
         pre-click tooltip and the post-click error use identical
         identifiers.
      4. State transition `complete → closed` (round-4 dropped
         `hold → closed`).
      5. Persist `closure_rationale` if provided.
      6. Audit `after_state` carries `closure_rationale` + status.

    SSE side-effect (UX spec §6.5.2 Completion): after the
    wo_state_changed event lands, a `done` event is published on
    every child op channel so any open Operation Execution
    subscriber cleanly closes — the op stream is permanently
    terminal once the WO is closed.

    `auth_context` is keyword-only and REQUIRED — without it the
    freshness gate cannot run.  Same posture as GC-3 round 5
    `record_sign_off`: there is no honest call site that needs to
    bypass the gate.
    """
    from forge.api.auth_freshness import (
        AuthFreshnessAction,
        require_fresh_auth,
    )
    from forge.services.closure_gate import check_closure_enabled

    # Gate 1: feature-flag governance gate.
    check_closure_enabled(settings)

    # Load the WO (program-scoped); the freshness + blocker checks
    # both need it before we touch state.
    wo_before = await get_work_order(
        session, wo_id, program_codes=program_codes,
    )

    # Gate 2: re-auth freshness for the protected close action.
    await require_fresh_auth(
        auth_context, session, AuthFreshnessAction.wo_close,
    )

    # Gate 3: closure-readiness blockers (NCRs + conditionals).
    blockers = await compute_closure_blockers(session, wo_before)
    if blockers:
        raise HTTPException(
            status_code=422,
            detail={
                "code": blockers[0]["code"],
                "message": (
                    "Work order is not ready to close: "
                    f"{len(blockers)} precondition(s) failed."
                ),
                "blockers": [
                    {
                        "code": b["code"],
                        "message": b["message"],
                        "entity_id": (
                            str(b["entity_id"])
                            if b.get("entity_id") is not None else None
                        ),
                    }
                    for b in blockers
                ],
            },
        )

    previous_state = wo_before.status.value

    # Gate 4 + persistence + audit: do the transition work inline
    # rather than going through `_transition_wo` so we can persist
    # `closure_rationale` in the same flush + carry it on the audit
    # row's `after_state`.  Keeps the rationale and the status flip
    # atomic — they MUST land together.
    try:
        check_version(wo_before, version, entity_type="WorkOrder")
    except VersionConflictError as e:
        raise version_conflict_response(
            e, current_entity=_wo_to_conflict_dict(wo_before),
        ) from e

    if not validate_wo_transition(wo_before.status, WOStatus.closed):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid transition: {wo_before.status.value} → "
                f"{WOStatus.closed.value}"
            ),
        )

    wo_before.status = WOStatus.closed
    if closure_rationale is not None:
        wo_before.closure_rationale = closure_rationale
    wo_before.updated_by = user_id
    increment_version(wo_before)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type=f"work_order.{WOStatus.closed.value}",
        entity_type="work_order",
        entity_id=wo_before.id,
        before_state={"status": previous_state},
        after_state={
            "status": WOStatus.closed.value,
            "closure_rationale": closure_rationale,
        },
    )
    wo = wo_before

    await _publish_wo_state_changed(
        session, wo, previous_state=previous_state, actor_id=user_id,
    )
    # Queue a `done` fan-out behind the wo_state_changed so clients
    # receive the state transition first, then the stream close.
    from forge.models.operation import Operation
    result = await session.execute(
        select(Operation.id).where(Operation.work_order_id == wo.id),
    )
    for target_op_id in [row[0] for row in result.all()]:
        def _publish_done(op: _uuid.UUID = target_op_id) -> Any:
            return OPERATION_BUS.publish(op, "done", {})

        queue_publish(session, _publish_done)
    return wo
