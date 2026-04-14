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

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, Disposition, NCRStatus
from forge.models.ncr import NonconformanceReport
from forge.rules.separation_of_duties import (
    SoDViolation,
    check_sod_002_ncr_disposition,
)
from forge.rules.status_transitions import validate_ncr_transition
from forge.schemas.ncr import NCRClose, NCRCreate, NCRDisposition
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


async def disposition_ncr(
    session: AsyncSession,
    ncr_id: _uuid.UUID,
    data: NCRDisposition,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> NonconformanceReport:
    """Disposition an NCR: open → dispositioned.

    State leaving: open.  State entering: dispositioned.
    Invariants (in order):
      1. Caller has program scope (via parent WO).
      2. Optimistic version matches.
      3. Transition is legal per VALID_NCR_TRANSITIONS.
      4. SOD-002: initiator must not equal the dispositioner.
      5. Disposition must be a concrete decision (not `pending`) so a
         disposition step cannot leave the NCR effectively undecided.
    """
    ncr = await _get_ncr_in_scope(session, ncr_id, program_codes)

    try:
        check_version(ncr, data.version, entity_type="NonconformanceReport")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

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

    before_status = ncr.status.value
    before_disposition = ncr.disposition.value
    ncr.status = NCRStatus.dispositioned
    ncr.disposition = data.disposition
    ncr.disposition_rationale = data.disposition_rationale
    ncr.engineering_approval_id = data.engineering_approval_id
    ncr.mrb_disposition = data.mrb_disposition
    ncr.mrb_authority_id = data.mrb_authority_id
    ncr.schedule_impact_days = data.schedule_impact_days
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
        },
    )

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
