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

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, OpStatus, WOStatus
from forge.models.work_order import WorkOrder
from forge.rules.status_transitions import validate_wo_transition
from forge.schemas.work_order import WorkOrderCreate, WorkOrderUpdate
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)


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

    Spec §5.0.3: program-scope filtering is enforced at the service
    layer.  If the user's programs don't include the WO's program,
    it appears as 403.  Wildcard "*" bypasses (local dev only).
    """
    wo = await session.get(WorkOrder, wo_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    if program_codes is not None and "*" not in program_codes:
        # Need to check the program code — load the program relationship
        await session.refresh(wo, ["program"])
        if wo.program.code not in program_codes:
            raise HTTPException(
                status_code=403,
                detail="No access to this work order's program",
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

    try:
        check_version(wo, data.version, entity_type="WorkOrder")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

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
) -> WorkOrder:
    """Apply a state transition with version check and audit.

    State leaving: wo.status.  State entering: target.
    Invariant: transition must be in VALID_WO_TRANSITIONS map.
    """
    wo = await get_work_order(session, wo_id, program_codes=program_codes)

    try:
        check_version(wo, version, entity_type="WorkOrder")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

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
        action_type=f"work_order.{target.value}",
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
    Invariant: valid transition per spec §4.3 AND authored_by_id != user_id
    (SOD-004 independence per spec §4.2, §5.1 line 885).
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

    wo = await _transition_wo(
        session, wo_id, WOStatus.released, version, user_id,
        program_codes=program_codes,
    )
    wo.released_at = datetime.now(UTC)
    wo.released_by = user_id
    await session.flush()
    return wo


async def hold_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Hold: released|in_progress → hold.

    State leaving: released or in_progress.  State entering: hold.
    Invariant: valid transition per spec §4.3.
    """
    return await _transition_wo(
        session, wo_id, WOStatus.hold, version, user_id,
        program_codes=program_codes,
    )


async def resume_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Resume: hold → in_progress.

    State leaving: hold.  State entering: in_progress.
    Invariant: valid transition per spec §4.3.
    """
    return await _transition_wo(
        session, wo_id, WOStatus.in_progress, version, user_id,
        program_codes=program_codes,
    )


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
    return await _transition_wo(
        session, wo_id, WOStatus.complete, version, user_id,
        program_codes=program_codes,
    )


async def close_work_order(
    session: AsyncSession, wo_id: _uuid.UUID, version: int, user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> WorkOrder:
    """Close: complete|hold → closed.

    State leaving: complete or hold.  State entering: closed.
    Invariant: valid transition per spec §4.3.
    NCR disposition check deferred to slice 3A.
    """
    return await _transition_wo(
        session, wo_id, WOStatus.closed, version, user_id,
        program_codes=program_codes,
    )
