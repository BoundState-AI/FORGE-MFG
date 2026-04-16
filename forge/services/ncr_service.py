#!/usr/bin/env python3
"""
Module:   NCR Service
Purpose:  Business logic for the NCR lifecycle: create, disposition, close.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin; the lifecycle gates live here so a future caller
  cannot bypass SOD-002 or the state-transition map by talking to the
  model layer.  Program scope is derived from the NCR's parent work
  order (NCR has no program_id of its own, mirroring how operations
  inherit scope), so the same §5.0.3 enforcement that protects the WO
  protects every NCR attached to it.

  The state machine is delegated to forge.rules.status_transitions —
  this module only hydrates context and asks the rule whether a move
  is legal.  SOD-002 is similarly delegated to the pure rule helper in
  forge.rules.separation_of_duties so the SoD policy has a single
  source of truth across slices.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING to avoid the runtime import cycle
    # against forge.api.auth_freshness → forge.api.auth → routes →
    # this module.  The actual freshness call is a lazy import inside
    # ``disposition_ncr`` for the same reason.
    from forge.api.auth import AuthContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.concurrency import (
    build_version_conflict_detail,
    composite_conflict_response,
)
from forge.audit.logger import create_audit_record
from forge.models.enums import (
    ActorType,
    Disposition,
    EvidenceScope,
    NCRStatus,
    OpStatus,
    WOStatus,
)
from forge.models.ncr import NonconformanceReport
from forge.models.operation import Operation
from forge.models.work_order import WorkOrder
from forge.rules.separation_of_duties import (
    SoDViolation,
    check_sod_002_ncr_disposition,
)
from forge.rules.status_transitions import validate_ncr_transition
from forge.schemas.ncr import NCRClose, NCRCreate, NCRDisposition, NCRPatch
from forge.services.evidence_service import hydrate_refs_for_parent
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)
from forge.services.work_order_service import get_work_order

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _generate_ncr_number(session: AsyncSession) -> str:
    """Generate the next NCR number in NCR-YYYY-NNNN format (spec §3.1).

    Why count-based: matches the WO numbering pattern for floor-staff
    familiarity.  Race condition risk is mitigated by the unique
    constraint on ncr_number — a duplicate raises IntegrityError that
    the caller can retry.  A DB sequence would be more robust but is
    not justified at expected NCR cadence.
    """
    year = datetime.now(UTC).year
    prefix = f"NCR-{year}-"
    result = await session.execute(
        select(func.count()).where(
            NonconformanceReport.ncr_number.startswith(prefix)
        )
    )
    count = result.scalar() or 0
    return f"{prefix}{count + 1:04d}"


def _violation_response(violation: SoDViolation) -> HTTPException:
    """Convert an SoD violation into the §5.15 422 RULE_VIOLATION envelope."""
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


async def _get_ncr_in_scope(
    session: AsyncSession,
    ncr_id: _uuid.UUID,
    program_codes: list[str] | None,
) -> NonconformanceReport:
    """Load an NCR and verify the caller's program scope via parent WO.

    Spec §5.0.3: program scope is enforced at the service layer.  An
    NCR has no program_id; its scope is the parent WO's program.  We
    delegate the scope check to work_order_service.get_work_order so
    every consumer of program scope uses one verified path.
    """
    ncr = await session.get(NonconformanceReport, ncr_id)
    if ncr is None:
        raise HTTPException(status_code=404, detail="NCR not found")
    # Cross-program access returns 404 (UX spec §3.2 / §6.7.4) — the
    # WO scope check below raises 404 if the caller is not in the
    # parent WO's program, indistinguishable from "no such NCR".
    await get_work_order(
        session, ncr.work_order_id, program_codes=program_codes
    )
    return ncr


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_ncr(
    session: AsyncSession,
    data: NCRCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> NonconformanceReport:
    """Create a new NCR in `open` status.

    State leaving: (none).  State entering: open.
    Invariants:
      1. Parent WO must exist and be in caller's program scope.
      2. NCR is created with status=open and disposition=pending.
      3. initiated_by_id is taken from the auth context (SOD-002 anchor).
    """
    # Program scope enforced by parent WO load.
    await get_work_order(
        session, data.work_order_id, program_codes=program_codes
    )

    ncr_number = await _generate_ncr_number(session)
    ncr = NonconformanceReport(
        ncr_number=ncr_number,
        work_order_id=data.work_order_id,
        operation_id=data.operation_id,
        part_instance_id=data.part_instance_id,
        description=data.description,
        status=NCRStatus.open,
        disposition=Disposition.pending,
        mrb_required=data.mrb_required,
        initiated_by_id=user_id,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(ncr)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="ncr.create",
        entity_type="ncr",
        entity_id=ncr.id,
        after_state={
            "status": ncr.status.value,
            "ncr_number": ncr.ncr_number,
            "work_order_id": str(ncr.work_order_id),
        },
    )
    return ncr


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_ncr(
    session: AsyncSession,
    ncr_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> NonconformanceReport:
    """Load a single NCR with program-scope enforcement."""
    return await _get_ncr_in_scope(session, ncr_id, program_codes)


async def list_ncrs(
    session: AsyncSession,
    *,
    program_codes: list[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[NonconformanceReport], int]:
    """List NCRs paginated, scoped via parent WO's program (spec §5.0.3).

    The join to WorkOrder + Program is the canonical way NCRs inherit
    scope — we never trust the client to filter, and the wildcard "*"
    bypass is local-dev only (matches list_work_orders).
    """
    from forge.models.program import Program
    from forge.models.work_order import WorkOrder

    base_query = select(NonconformanceReport)
    count_query = select(func.count(NonconformanceReport.id))

    if program_codes is not None and "*" not in program_codes:
        base_query = (
            base_query.join(
                WorkOrder, WorkOrder.id == NonconformanceReport.work_order_id
            )
            .join(Program, Program.id == WorkOrder.program_id)
            .where(Program.code.in_(program_codes))
        )
        count_query = (
            count_query.join(
                WorkOrder, WorkOrder.id == NonconformanceReport.work_order_id
            )
            .join(Program, Program.id == WorkOrder.program_id)
            .where(Program.code.in_(program_codes))
        )

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(
        base_query
        .order_by(NonconformanceReport.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


# ---------------------------------------------------------------------------
# Disposition
# ---------------------------------------------------------------------------


_REOPEN_DISPOSITIONS: frozenset[Disposition] = frozenset({
    Disposition.rework, Disposition.repair,
})


async def _validate_remediation_wo(
    session: AsyncSession,
    data: NCRDisposition,
    ncr: NonconformanceReport,
    program_codes: list[str] | None,
) -> WorkOrder:
    """Validate a remediation WO reference on the closed-WO path.

    Build spec v1.4.2 GC-2: when the NCR's parent WO is closed and
    disposition is rework / repair, the client must name a separate
    remediation WO on the disposition request.  This helper enforces:
      1. Field present (422 REMEDIATION_WO_REQUIRED otherwise).
      2. Remediation WO exists + caller has scope (404 → 422 to
         keep the error on the disposition surface).
      3. Remediation WO is in the SAME program as the NCR's parent
         WO — remediation cannot cross program boundaries.
      4. Remediation WO is not the NCR's parent WO itself.
    """
    from forge.services.work_order_service import get_work_order

    if data.remediation_work_order_id is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REMEDIATION_WO_REQUIRED",
                "message": (
                    "Parent work order is closed; rework/repair "
                    "disposition requires remediation_work_order_id"
                ),
            },
        )
    if data.remediation_work_order_id == ncr.work_order_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REMEDIATION_WO_INVALID",
                "message": (
                    "remediation_work_order_id must be a different "
                    "work order than the NCR's parent WO"
                ),
            },
        )
    try:
        remediation_wo: WorkOrder = await get_work_order(
            session, data.remediation_work_order_id,
            program_codes=program_codes,
        )
    except HTTPException as exc:
        # get_work_order raises 404 on scope miss; restate as 422 on
        # the disposition surface so the UI handles the single 422
        # branch rather than a surprise 404 from a write endpoint.
        if exc.status_code == 404:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "REMEDIATION_WO_INVALID",
                    "message": (
                        "remediation_work_order_id does not reference "
                        "a visible work order"
                    ),
                },
            ) from exc
        raise

    # Parent WO's program drives the allowed scope — remediation must
    # happen within the same program as the nonconformance.
    parent_wo = await get_work_order(
        session, ncr.work_order_id, program_codes=program_codes,
    )
    if remediation_wo.program_id != parent_wo.program_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REMEDIATION_WO_INVALID",
                "message": (
                    "remediation_work_order_id belongs to another program"
                ),
            },
        )
    return remediation_wo


async def _reopen_operation(
    session: AsyncSession,
    op: Operation,
    ncr: NonconformanceReport,
    user_id: _uuid.UUID,
) -> bool:
    """Direct complete → in_progress reopen for a linked operation.

    Build spec v1.4.2 GC-2 / UX spec v1.5.1 §5.4.1: the live reopen
    path is a direct transition.  No persisted `rework` state is
    introduced — the op moves straight to in_progress, execution_cycle
    increments, completion-derived fields are cleared, and
    active_since resets so the overdue clock measures the new cycle.

    Returns True iff the operation was actually reopened (was in
    `complete` and transitioned).  Idempotent on a non-complete op
    (in_progress / hold / pending) — returns False without mutation
    so a second disposition on the same op cannot double-bump cycle.
    """
    from datetime import datetime

    if op.status != OpStatus.complete:
        return False

    before_status = op.status.value
    before_cycle = op.execution_cycle
    now = datetime.now(UTC)

    op.status = OpStatus.in_progress
    op.execution_cycle = before_cycle + 1
    op.completed_at = None
    op.active_since = now
    op.updated_by = user_id
    increment_version(op)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="operation.reopened",
        entity_type="operation",
        entity_id=op.id,
        before_state={
            "status": before_status,
            "execution_cycle": before_cycle,
        },
        after_state={
            "status": op.status.value,
            "execution_cycle": op.execution_cycle,
            "ncr_id": str(ncr.id),
            "disposition": ncr.disposition.value,
        },
    )

    # SSE publish (UX spec v1.5.1 §6.5.2 operation_reopened).  The
    # Operation Execution stream carries a dedicated reopen event
    # with the NCR + disposition context so the frontend can render
    # the reopen banner without a second REST fetch.  Deferred to
    # after-commit via queue_publish so subscribers never see a
    # rolled-back reopen.
    from forge.realtime import OPERATION_BUS, queue_publish

    queue_publish(
        session,
        lambda: OPERATION_BUS.publish(
            op.id,
            "operation_reopened",
            {
                "operation_id": str(op.id),
                "ncr_id": str(ncr.id),
                "disposition": ncr.disposition.value,
                "execution_cycle": op.execution_cycle,
            },
        ),
    )
    return True


async def _reactivate_work_order(
    session: AsyncSession,
    wo: WorkOrder,
    ncr: NonconformanceReport,
    user_id: _uuid.UUID,
) -> bool:
    """Direct complete → in_progress reactivate for a parent WO.

    Build spec v1.4.2 GC-2: when a child op reopens and the parent
    WO is `complete`, the WO reactivates in place.  `closed` WOs
    are NEVER reactivated in Phase 1 — the NCR's
    remediation_work_order_id branch handles those.

    Returns True iff the WO was actually reactivated.
    """
    if wo.status != WOStatus.complete:
        return False

    before_status = wo.status.value
    wo.status = WOStatus.in_progress
    wo.updated_by = user_id
    increment_version(wo)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="work_order.reactivated",
        entity_type="work_order",
        entity_id=wo.id,
        before_state={"status": before_status},
        after_state={
            "status": wo.status.value,
            "ncr_id": str(ncr.id),
            "disposition": ncr.disposition.value,
        },
    )

    # Reuse the canonical wo_state_changed SSE surface so the
    # frontend's existing WO-state handler renders the reactivation
    # without a second event name.  The payload includes the NCR
    # context via `reason` so the stop-work-style banner has
    # attribution — no new event type is introduced.
    from forge.realtime import (
        publish_wo_state_changed_to_ops,
        resolve_actor,
    )

    actor = await resolve_actor(session, user_id)
    await publish_wo_state_changed_to_ops(
        session, wo.id,
        {
            "work_order_id": str(wo.id),
            "state": wo.status.value,
            "previous_state": before_status,
            "actor": {
                "user_id": actor["id"],
                "display_name": actor["display_name"],
            },
            "reason": (
                f"NCR {ncr.ncr_number} dispositioned "
                f"{ncr.disposition.value}"
            ),
            "force_stop_labor": False,
            "stopped_labor_entries": [],
        },
    )
    return True


async def _apply_reopen_side_effect(
    session: AsyncSession,
    ncr: NonconformanceReport,
    data: NCRDisposition,
    user_id: _uuid.UUID,
    program_codes: list[str] | None,
) -> None:
    """Trigger op reopen + WO reactivate side effects if applicable.

    Branching (build spec v1.4.2 GC-2):
      - disposition ∈ {use_as_is, scrap}: no-op.
      - disposition ∈ {rework, repair}:
          a. If the NCR names no operation, no-op — there is no
             operation to reopen and the caller has made an
             informed decision at NCR creation.
          b. If the NCR's parent WO is `closed`, the client must
             supply remediation_work_order_id (validated in the
             caller); the closed WO is NOT reactivated.
          c. Otherwise, reopen the linked operation if it is
             `complete`, and reactivate the parent WO if it is
             `complete`.  Idempotent on both axes — an already-
             in_progress op / WO is untouched.
    """
    from forge.services.work_order_service import get_work_order

    if data.disposition not in _REOPEN_DISPOSITIONS:
        return
    if ncr.operation_id is None:
        return

    parent_wo = await get_work_order(
        session, ncr.work_order_id, program_codes=program_codes,
    )
    if parent_wo.status == WOStatus.closed:
        # Closed-WO path: the client's remediation_work_order_id is
        # recorded for close-gating; nothing is reopened here.
        return

    from forge.models.operation import Operation
    op = await session.get(Operation, ncr.operation_id)
    if op is None:
        return

    reopened = await _reopen_operation(session, op, ncr, user_id)
    if reopened:
        await _reactivate_work_order(
            session, parent_wo, ncr, user_id,
        )


async def _check_disposition_version_assertions(
    session: AsyncSession,
    ncr: NonconformanceReport,
    data: NCRDisposition,
) -> None:
    """UX spec v1.5.1 §6.3.3 composite check for NCR disposition.

    Validates every supplied assertion against the live row BEFORE
    any mutation.  A single stale version triggers a composite 409
    listing every mismatched entity (NCR, operation, work_order) so
    the client can render per-entity diffs without a second round
    trip.  Only entities named in `version_assertions` are checked
    — absent assertions fall through to the existing single-entity
    version handling.

    Serializes fresh entity snapshots using the matching XxxOut
    schemas so the §6.3.2 `current_entity` field carries the shape
    the read endpoints would return.
    """
    assertions = data.version_assertions
    if assertions is None:
        return

    from forge.models.operation import Operation
    from forge.models.work_order import WorkOrder
    from forge.schemas.ncr import NCROut
    from forge.schemas.operation import OperationOut
    from forge.schemas.work_order import WorkOrderOut

    conflicts: list[dict[str, object]] = []

    if (
        assertions.ncr is not None
        and ncr.version != assertions.ncr
    ):
        conflicts.append(build_version_conflict_detail(
            entity_type="NonconformanceReport",
            entity_id=str(ncr.id),
            your_version=assertions.ncr,
            current_version=ncr.version,
            current_entity=NCROut.model_validate(ncr).model_dump(
                mode="json",
            ),
        ))

    if assertions.operation is not None:
        if ncr.operation_id is None:
            # Client asserted an operation version for an NCR that
            # has no linked operation — reject as a mis-built
            # request rather than silently skip.
            conflicts.append(build_version_conflict_detail(
                entity_type="Operation",
                entity_id="",
                your_version=assertions.operation,
                current_version=0,
                current_entity=None,
            ))
        else:
            op = await session.get(Operation, ncr.operation_id)
            if op is None:
                conflicts.append(build_version_conflict_detail(
                    entity_type="Operation",
                    entity_id=str(ncr.operation_id),
                    your_version=assertions.operation,
                    current_version=0,
                    current_entity=None,
                ))
            elif op.version != assertions.operation:
                conflicts.append(build_version_conflict_detail(
                    entity_type="Operation",
                    entity_id=str(op.id),
                    your_version=assertions.operation,
                    current_version=op.version,
                    current_entity=OperationOut.model_validate(op).model_dump(
                        mode="json",
                    ),
                ))

    if assertions.work_order is not None:
        wo = await session.get(WorkOrder, ncr.work_order_id)
        if wo is None:
            conflicts.append(build_version_conflict_detail(
                entity_type="WorkOrder",
                entity_id=str(ncr.work_order_id),
                your_version=assertions.work_order,
                current_version=0,
                current_entity=None,
            ))
        elif wo.version != assertions.work_order:
            conflicts.append(build_version_conflict_detail(
                entity_type="WorkOrder",
                entity_id=str(wo.id),
                your_version=assertions.work_order,
                current_version=wo.version,
                current_entity=WorkOrderOut.model_validate(wo).model_dump(
                    mode="json",
                ),
            ))

    if conflicts:
        raise composite_conflict_response(conflicts)


async def disposition_ncr(
    session: AsyncSession,
    ncr_id: _uuid.UUID,
    data: NCRDisposition,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
    *,
    auth_context: AuthContext | None = None,
) -> NonconformanceReport:
    """Disposition an NCR: open → dispositioned + optional reopen.

    State leaving: open.  State entering: dispositioned.
    Invariants (in order):
      1. Caller has program scope (via parent WO).
      2. GC-3 freshness gate (when auth_context is provided): caller's
         auth_time must be within ``MAX_AUTH_AGE_SECONDS``.  Stale auth
         raises ``StaleAuthError`` (401 REAUTH_REQUIRED) BEFORE the
         version check, SoD check, or any side effect — so the same
         Idempotency-Key retry after fresh login lands on a clean
         slate (UX spec §11.3).  Unconditional: NCR disposition is
         always a protected action per §9.2 (not just safety-critical).
      3. Optimistic version matches.
      4. Transition is legal per VALID_NCR_TRANSITIONS.
      5. SOD-002: initiator must not equal the dispositioner.
      6. Disposition must be a concrete decision (not `pending`).
      7. If disposition is rework/repair AND parent WO is closed,
         remediation_work_order_id must be supplied and valid.

    Side effects (build spec v1.4.2 GC-2):
      - rework/repair on an open-or-complete parent WO with a
        linked operation: the op reopens (complete → in_progress,
        execution_cycle++, completed_at cleared, active_since
        reset) and the WO reactivates if it was complete.
      - rework/repair on a CLOSED parent WO: the
        remediation_work_order_id is persisted; the closed WO
        is NOT reactivated.  Close of this NCR is subsequently
        gated on the remediation WO reaching `closed`.
      - use_as_is / scrap: no reopen side effect.
    """
    ncr = await _get_ncr_in_scope(session, ncr_id, program_codes)

    # GC-3: freshness gate fires AFTER scope check (so 404-not-403
    # cross-program semantics from §6.7.4 still take precedence) but
    # BEFORE the version check, SoD check, or any side effect.  Spec
    # §11.3: this ordering is what makes the same-Idempotency-Key
    # retry after fresh auth safe.
    if auth_context is not None:
        from forge.api.auth_freshness import (
            AuthFreshnessAction,
            require_fresh_auth,
        )

        await require_fresh_auth(
            auth_context, session, AuthFreshnessAction.ncr_disposition
        )

    try:
        check_version(ncr, data.version, entity_type="NonconformanceReport")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    # UX spec v1.5.1 §6.3.3 composite concurrency: when the client
    # supplied per-entity version_assertions, validate ALL of them
    # up-front so a single stale version surfaces as a composite
    # 409 before any mutation fires.  The legacy single-NCR
    # `version` field above remains the primary gate; this is
    # additive.
    await _check_disposition_version_assertions(session, ncr, data)

    if not validate_ncr_transition(ncr.status, NCRStatus.dispositioned):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid transition: {ncr.status.value} → "
                f"{NCRStatus.dispositioned.value}"
            ),
        )

    if data.disposition == Disposition.pending:
        raise HTTPException(
            status_code=422,
            detail="Disposition decision cannot be `pending` at disposition step",
        )

    sod_violation = check_sod_002_ncr_disposition(
        initiator_id=ncr.initiated_by_id, dispositioner_id=user_id
    )
    if sod_violation is not None:
        raise _violation_response(sod_violation)

    # Closed-WO branch validation: when the NCR's parent WO is
    # already closed and disposition is rework/repair, the client
    # MUST provide a valid remediation_work_order_id.  Resolution
    # happens BEFORE the NCR mutation so a failed validation leaves
    # the NCR untouched.
    from forge.services.work_order_service import get_work_order
    parent_wo = await get_work_order(
        session, ncr.work_order_id, program_codes=program_codes,
    )
    if (
        parent_wo.status == WOStatus.closed
        and data.disposition in _REOPEN_DISPOSITIONS
    ):
        await _validate_remediation_wo(session, data, ncr, program_codes)

    before_status = ncr.status.value
    before_disposition = ncr.disposition.value
    ncr.status = NCRStatus.dispositioned
    ncr.disposition = data.disposition
    ncr.disposition_rationale = data.disposition_rationale
    ncr.engineering_approval_id = data.engineering_approval_id
    ncr.mrb_disposition = data.mrb_disposition
    ncr.mrb_authority_id = data.mrb_authority_id
    ncr.schedule_impact_days = data.schedule_impact_days
    # Persist remediation linkage regardless of WO status — a
    # non-closed WO with a remediation link is an edge case the
    # spec does not prohibit and the close gate still honors.
    ncr.remediation_work_order_id = data.remediation_work_order_id
    ncr.updated_by = user_id
    increment_version(ncr)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="ncr.disposition",
        entity_type="ncr",
        entity_id=ncr.id,
        before_state={
            "status": before_status, "disposition": before_disposition,
        },
        after_state={
            "status": ncr.status.value,
            "disposition": ncr.disposition.value,
            "remediation_work_order_id": (
                str(ncr.remediation_work_order_id)
                if ncr.remediation_work_order_id else None
            ),
        },
    )

    # Trigger the reopen / reactivate side effect inside the same
    # transaction so the NCR disposition, op reopen, WO reactivate,
    # audit rows, and queued SSE publishes all commit atomically.
    await _apply_reopen_side_effect(session, ncr, data, user_id, program_codes)

    # Spec §8.2: NCR status changes trigger CPM recalculation via the
    # outbox.  Aggregate is the NCR itself; the handler walks up to
    # the parent WO to find the schedule.
    from forge.events.bus import emit_event
    from forge.models.enums import EventType

    await emit_event(
        session,
        event_type=EventType.ncr_status_changed,
        aggregate_type="ncr",
        aggregate_id=str(ncr.id),
        payload={
            "before": before_status,
            "after": ncr.status.value,
        },
    )
    return ncr


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


async def close_ncr(
    session: AsyncSession,
    ncr_id: _uuid.UUID,
    data: NCRClose,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> NonconformanceReport:
    """Close an NCR: dispositioned → closed.

    State leaving: dispositioned.  State entering: closed.
    Invariants (in order):
      1. Caller has program scope (via parent WO).
      2. Optimistic version matches.
      3. Transition is legal per VALID_NCR_TRANSITIONS — closing an
         `open` NCR is rejected here, so an undecided NCR cannot be
         silently buried.
    """
    ncr = await _get_ncr_in_scope(session, ncr_id, program_codes)

    try:
        check_version(ncr, data.version, entity_type="NonconformanceReport")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if not validate_ncr_transition(ncr.status, NCRStatus.closed):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid transition: {ncr.status.value} → "
                f"{NCRStatus.closed.value}"
            ),
        )

    # Build spec v1.4.2 Decision 1: close requires non-empty evidence
    # for rework / repair dispositions.  use_as_is / scrap remain
    # closeable without evidence.  Pending is blocked by the
    # transition map (can't close an open NCR), so disposition is
    # guaranteed to be one of use_as_is / rework / repair / scrap
    # at this point.
    if (
        ncr.disposition in (Disposition.rework, Disposition.repair)
        and not ncr.evidence_refs
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVIDENCE_REQUIRED_FOR_CLOSE",
                "message": (
                    "Completion evidence is required to close a "
                    f"{ncr.disposition.value}-dispositioned NCR; "
                    "attach evidence via "
                    "PATCH /api/ncrs/{id} before close"
                ),
            },
        )

    # Build spec v1.4.2 GC-2 closed-WO branch: if the NCR carries a
    # remediation_work_order_id (set because the parent WO was
    # already closed at disposition time), close is blocked until
    # the remediation WO itself reaches `closed`.  Callers that
    # need to surface this precondition separately can still GET
    # the remediation WO directly.
    if ncr.remediation_work_order_id is not None:
        remediation_wo = await get_work_order(
            session, ncr.remediation_work_order_id,
            program_codes=program_codes,
        )
        if remediation_wo.status != WOStatus.closed:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "REMEDIATION_WO_NOT_CLOSED",
                    "message": (
                        "Remediation work order must be closed "
                        "before this NCR can close"
                    ),
                    "remediation_work_order_id": str(
                        ncr.remediation_work_order_id
                    ),
                    "remediation_work_order_status": (
                        remediation_wo.status.value
                    ),
                },
            )

    before_status_close = ncr.status.value
    ncr.status = NCRStatus.closed
    if data.root_cause is not None:
        ncr.root_cause = data.root_cause
    ncr.updated_by = user_id
    increment_version(ncr)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="ncr.close",
        entity_type="ncr",
        entity_id=ncr.id,
        before_state={"status": before_status_close},
        after_state={"status": ncr.status.value},
    )

    # Spec §8.2: NCR status change triggers CPM recalc via outbox.
    from forge.events.bus import emit_event
    from forge.models.enums import EventType

    await emit_event(
        session,
        event_type=EventType.ncr_status_changed,
        aggregate_type="ncr",
        aggregate_id=str(ncr.id),
        payload={
            "before": before_status_close,
            "after": ncr.status.value,
        },
    )
    return ncr


# ---------------------------------------------------------------------------
# Patch (build spec v1.4.2 Decision 1 — evidence write contract)
# ---------------------------------------------------------------------------


async def patch_ncr(
    session: AsyncSession,
    ncr_id: _uuid.UUID,
    data: NCRPatch,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> NonconformanceReport:
    """Replace evidence_refs on an NCR that is not yet closed.

    State leaving: ncr.status ∈ {open, dispositioned}.
    State entering: same status; evidence_refs replaced with the
    hydrated canonical refs from the evidence_confirmations registry.

    Invariants (in order):
      1. NCR exists and caller has program scope (404 on mismatch).
      2. Optimistic version matches.
      3. NCR is not closed — closed NCRs are terminal.
      4. Submitted refs are structured (no bare strings).
      5. Each ref resolves to a confirmed registry row whose scope
         is ncr_evidence and whose program matches the parent WO's
         program.

    Replace semantics (not append): the client sends the full
    desired list; the server persists exactly that list after
    hydration.  This matches the UX spec §5.6.4 remediation-
    evidence list pattern.
    """
    ncr = await _get_ncr_in_scope(session, ncr_id, program_codes)

    try:
        check_version(ncr, data.version, entity_type="NonconformanceReport")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if ncr.status == NCRStatus.closed:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "NCR_CLOSED_IMMUTABLE",
                "message": "Closed NCRs cannot be modified",
            },
        )

    wo = await get_work_order(
        session, ncr.work_order_id, program_codes=program_codes,
    )
    hydrated_refs = await hydrate_refs_for_parent(
        session=session,
        submitted=list(data.evidence_refs),
        expected_scope=EvidenceScope.ncr_evidence,
        expected_program_id=wo.program_id,
    )

    before_refs = list(ncr.evidence_refs)
    ncr.evidence_refs = hydrated_refs
    ncr.updated_by = user_id
    increment_version(ncr)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="ncr.evidence_updated",
        entity_type="ncr",
        entity_id=ncr.id,
        before_state={"evidence_count": len(before_refs)},
        after_state={"evidence_count": len(hydrated_refs)},
    )
    return ncr
