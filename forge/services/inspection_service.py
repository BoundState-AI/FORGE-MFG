#!/usr/bin/env python3
"""
Module:   Inspection Service
Purpose:  Business logic for recording inspections and sign-offs on
          operations, enforcing SOD-001 and SOD-005 (spec §5.4, §4.2).

BoundState AI 2026
Version: 0.1.0
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
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, OpStatus
from forge.models.inspection import InspectionEvent, SignOffEvent
from forge.models.labor import LaborEntry
from forge.models.operation import Operation
from forge.rules.separation_of_duties import (
    SignOffSnapshot as SoDSignOff,
)
from forge.rules.separation_of_duties import (
    SoDViolation,
    check_sod_001_self_inspection,
    check_sod_005_dual_signoff,
)
from forge.schemas.inspection import InspectionCreate, SignOffCreate
from forge.services.operation_service import _get_op


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
    """
    if op.status != OpStatus.in_progress:
        raise HTTPException(
            status_code=422,
            detail=(
                "Inspections and sign-offs may only be recorded while the "
                "operation is in_progress"
            ),
        )


async def _labor_user_ids(
    session: AsyncSession, op_id: _uuid.UUID, execution_cycle: int
) -> list[_uuid.UUID]:
    """Load distinct user_ids that have labor entries on this operation.

    SOD-001 considers any labor on the operation.  Note: labor is not
    cycle-scoped in the data model (a single LaborEntry can span a
    rework boundary), so this is intentionally not filtered by
    execution_cycle.  The cycle parameter is kept for symmetry with
    sign-off retrieval and to make a future cycle-scoped policy a
    one-line change.
    """
    del execution_cycle  # reserved for a future cycle-scoped variant
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


async def record_inspection(
    session: AsyncSession,
    op_id: _uuid.UUID,
    data: InspectionCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> InspectionEvent:
    """Record an inspection event against an in-progress operation.

    State leaving: in_progress.  State entering: in_progress.
    Invariants:
      1. Op must be in_progress (recording window is the active cycle).
      2. SOD-001: inspector must not have labor entries on this op.
      3. execution_cycle is taken server-side from op.execution_cycle.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)
    _require_op_in_progress(op)

    labor_users = await _labor_user_ids(session, op_id, op.execution_cycle)
    sod_violation = check_sod_001_self_inspection(
        inspector_id=user_id, labor_user_ids=labor_users
    )
    if sod_violation is not None:
        raise _violation_response(sod_violation)

    now = datetime.now(UTC)
    event = InspectionEvent(
        operation_id=op_id,
        inspector_id=user_id,
        execution_cycle=op.execution_cycle,
        criteria=data.criteria,
        result=data.result,
        measurements=data.measurements,
        evidence_refs=data.evidence_refs,
        inspected_at=now,
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
        user_role="inspector",
        action_type="inspection.record",
        entity_type="inspection_event",
        entity_id=event.id,
        after_state={
            "operation_id": str(op_id),
            "execution_cycle": op.execution_cycle,
            "result": data.result.value,
        },
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
) -> SignOffEvent:
    """Record a role-based sign-off against an in-progress operation.

    State leaving: in_progress.  State entering: in_progress.
    Invariants:
      1. Op must be in_progress.
      2. SOD-005 (only if safety_critical): the new (user_id, role) pair
         cannot reuse a user_id already present on a *different* role
         in the current execution_cycle.  Re-signing the same role is
         rejected by the unique DB constraint, not by SoD.
      3. execution_cycle is taken server-side from op.execution_cycle.
    """
    op = await _get_op(session, op_id, program_codes=program_codes)
    _require_op_in_progress(op)

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
