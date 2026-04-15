#!/usr/bin/env python3
"""
Module:   Stop Request Service
Purpose:  Business logic for force-stop, stop-request, and
          flag-correction (UX spec v1.5.1 §5.4.5, §5.4.9).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  One service owns the full labor-control contract:

    - apply_force_stop: engineer stops another user's active entry.
    - apply_stop_request: operator asks an engineer to stop one.
    - apply_flag_correction: operator flags an entry for engineer
      review (no state transition; audit-only).
    - stop_labor_entry_and_resolve: shared helper that both
      force-stop and WO-hold cascade call into — stops the entry,
      increments version, transitions pending stop-requests
      (fulfilled on force-stop path, expired on other paths).

  The shared helper is load-bearing: spec §5.4.9 Stop-Request
  Lifecycle requires server-managed transitions "when a labor
  entry is stopped (by any mechanism)."  Two independent stop
  code paths would drift as soon as a new stop path shows up
  (session timeout, admin kill, etc.).  A single helper with a
  typed `fulfillment_mode` parameter is the honest single source
  of truth.

  Authorization rules are spec-exact and split three ways:

    - engineer for force-stop (labor:force_stop — qa_manager's
      labor:update_any does NOT imply this per §5.4.9
      LABOR-STOP-003).
    - operator for stop-request and flag-correction
      (labor:request_stop, labor:flag_correction).

  "Cannot act on own entry" is enforced here in the service rather
  than at the route so a future internal caller that skips the
  route dep still hits the check.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, StopRequestStatus
from forge.models.labor import LaborEntry
from forge.models.stop_request import StopRequest
from forge.models.user import User
from forge.schemas.stop_request import (
    FlagCorrectionIn,
    ForceStopIn,
    StopRequestActor,
    StopRequestIn,
    StopRequestOut,
)
from forge.services.operation_service import _get_op
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)


class StopFulfillmentMode(Enum):
    """How a labor-entry stop resolves its pending stop-requests.

    FORCE_STOP: pending → fulfilled (engineer acted on the request).
    OTHER: pending → expired (self-stop, hold cascade, timeout, etc.).

    Enum rather than bool because a third resolution mode could land
    in the future (e.g. "cancelled by operator" once self-cancel
    lands); an enum extends without a signature break.
    """

    FORCE_STOP = "force_stop"
    OTHER = "other"


@dataclass(frozen=True)
class StoppedEntrySummary:
    """Compact record returned from the shared stop helper.

    Carries only the fields WO-hold cascade needs for its response
    schema: labor entry id, parent operation id, target user id,
    and the stopped_at timestamp (so the response can build the
    per-entry snapshot without re-reading the row).
    """

    labor_entry_id: _uuid.UUID
    operation_id: _uuid.UUID
    user_id: _uuid.UUID
    stopped_at: datetime


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


async def _get_labor_entry(
    session: AsyncSession, labor_id: _uuid.UUID,
) -> LaborEntry:
    """Load a LaborEntry by id or raise 404.

    Centralizing the lookup lets the three endpoints share one
    not-found shape (standard detail: "Labor entry not found")
    instead of each re-rolling it.
    """
    labor = await session.get(LaborEntry, labor_id)
    if labor is None:
        raise HTTPException(
            status_code=404, detail="Labor entry not found",
        )
    return labor


async def _enforce_labor_program_scope(
    session: AsyncSession, labor: LaborEntry,
    program_codes: list[str] | None,
) -> None:
    """Walk labor -> operation -> WO for the §6.7.4 program check.

    A labor entry carries no program_id directly; program membership
    is inherited through the parent operation's WO.  Reusing
    operation_service._get_op keeps this single-source-of-truth and
    returns 404 on cross-program per §3.2 existence-leak rule.
    """
    await _get_op(session, labor.operation_id, program_codes=program_codes)


def _rule_violation(code: str, message: str) -> HTTPException:
    """Standard §5.15 RULE_VIOLATION envelope for labor-control errors.

    Every LABOR-STOP-* / LABOR-STOP-REQ-* code hits the same client
    handler; keeping the shape uniform means one parser wire path.
    """
    return HTTPException(
        status_code=422,
        detail={
            "code": "RULE_VIOLATION",
            "message": message,
            "violations": [{
                "rule_id": code,
                "rule_name": code,
                "severity": "block",
                "message": message,
                "context": {},
            }],
        },
    )


async def _resolve_requester_display_name(
    session: AsyncSession, user_id: _uuid.UUID,
) -> str:
    """Look up a user's display_name for StopRequestOut.requested_by.

    Falls back to "" only if the user row is missing — a defensive
    branch that should never fire in practice (FK enforces the
    reverse).  Returning an empty string rather than None keeps
    the schema's required str type happy.
    """
    user = await session.get(User, user_id)
    return user.display_name if user is not None else ""


# ---------------------------------------------------------------------------
# Shared stop helper — single source of truth for stop transitions
# ---------------------------------------------------------------------------


async def stop_labor_entry_and_resolve(
    session: AsyncSession,
    labor: LaborEntry,
    actor_id: _uuid.UUID,
    *,
    fulfillment_mode: StopFulfillmentMode,
    stopped_at: datetime,
) -> StoppedEntrySummary:
    """Stop an active labor entry and transition pending stop-requests.

    State leaving: labor.stopped_at=None.  State entering:
    labor.stopped_at=stopped_at.  Invariants enforced by caller:
      - target entry must be active (caller raises LABOR-STOP-001
        / LABOR-STOP-REQ-001 BEFORE invoking this helper).
      - version check (optimistic lock) happens in the caller; this
        helper does not re-check to avoid double-incrementing.

    Pending stop-requests transition per spec §5.4.9 Stop-Request
    Lifecycle based on `fulfillment_mode`:
      - FORCE_STOP → fulfilled (engineer acted).
      - OTHER      → expired (any other stop mechanism).

    Hours are rolled in here so every stop path produces the same
    computed duration (prior code had hours math inline in
    stop_operation, which would drift from the force-stop path
    otherwise).
    """
    labor.stopped_at = stopped_at
    elapsed = (stopped_at - labor.started_at).total_seconds() / 3600.0
    labor.hours = round(elapsed, 2)
    labor.updated_by = actor_id
    increment_version(labor)

    # Transition every pending stop-request on this entry.  A single
    # UPDATE would be faster but we need per-row audit events and
    # the stop-request count is always small (at most 1 pending by
    # DB partial-unique index, but loop kept inclusive in case a
    # data bug slipped past).
    target_status = (
        StopRequestStatus.fulfilled
        if fulfillment_mode is StopFulfillmentMode.FORCE_STOP
        else StopRequestStatus.expired
    )
    pending_result = await session.execute(
        select(StopRequest).where(
            StopRequest.labor_entry_id == labor.id,
            StopRequest.status == StopRequestStatus.pending,
        ),
    )
    pending = list(pending_result.scalars().all())
    for req in pending:
        req.status = target_status
        req.updated_by = actor_id
        increment_version(req)

    await session.flush()

    return StoppedEntrySummary(
        labor_entry_id=labor.id,
        operation_id=labor.operation_id,
        user_id=labor.user_id,
        stopped_at=stopped_at,
    )


# ---------------------------------------------------------------------------
# Force-stop (engineer)
# ---------------------------------------------------------------------------


async def apply_force_stop(
    session: AsyncSession,
    labor_id: _uuid.UUID,
    data: ForceStopIn,
    actor_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> LaborEntry:
    """Engineer force-stops another user's active labor entry.

    Invariants (in order):
      1. Entry exists, program scope (404 on cross-program).
      2. Entry not already stopped (LABOR-STOP-001).
      3. Entry belongs to a different user (LABOR-STOP-002).
      4. Version matches (409 VERSION_CONFLICT).
      5. Persist stop + fulfill pending stop-requests.
      6. Audit labor.stopped_by_engineer with rationale + target
         user + entry timestamps.

    LABOR-STOP-003 ("not engineer") is enforced at the route by
    the `labor:force_stop` permission dependency — this code path
    is only reached when the permission has already passed.
    """
    labor = await _get_labor_entry(session, labor_id)
    await _enforce_labor_program_scope(session, labor, program_codes)

    if labor.stopped_at is not None:
        raise _rule_violation(
            "LABOR-STOP-001",
            "Labor entry is already stopped",
        )
    if labor.user_id == actor_id:
        raise _rule_violation(
            "LABOR-STOP-002",
            "Cannot force-stop your own labor entry",
        )

    # Route-layer resolve_version_assertion already guarantees int.
    assert data.labor_entry_version is not None, (
        "route must resolve version before service"
    )
    try:
        check_version(
            labor, data.labor_entry_version, entity_type="LaborEntry",
        )
    except VersionConflictError as e:
        from forge.schemas.labor import LaborOut
        raise version_conflict_response(
            e,
            current_entity=LaborOut.model_validate(labor).model_dump(
                mode="json",
            ),
        ) from e

    now = datetime.now(UTC)
    summary = await stop_labor_entry_and_resolve(
        session, labor, actor_id,
        fulfillment_mode=StopFulfillmentMode.FORCE_STOP,
        stopped_at=now,
    )

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role="engineer",
        action_type="labor.stopped_by_engineer",
        entity_type="labor_entry",
        entity_id=labor.id,
        before_state={"stopped_at": None},
        after_state={
            "stopped_at": summary.stopped_at.isoformat(),
            "target_user_id": str(labor.user_id),
            "rationale": data.rationale,
        },
    )

    return labor


# ---------------------------------------------------------------------------
# Stop-request (operator)
# ---------------------------------------------------------------------------


async def apply_stop_request(
    session: AsyncSession,
    labor_id: _uuid.UUID,
    data: StopRequestIn,
    actor_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> StopRequestOut:
    """Operator requests an engineer to stop another user's entry.

    Invariants (in order):
      1. Entry exists, program scope (404 on cross-program).
      2. Entry not already stopped (LABOR-STOP-REQ-001).
      3. Entry belongs to a different user (LABOR-STOP-REQ-003).
      4. Version matches (409 VERSION_CONFLICT).
      5. Create stop-request; DB partial-unique index catches a
         concurrent duplicate (LABOR-STOP-REQ-002).
      6. Audit labor.stop_requested with requester + target user
         + note.
    """
    labor = await _get_labor_entry(session, labor_id)
    await _enforce_labor_program_scope(session, labor, program_codes)

    if labor.stopped_at is not None:
        raise _rule_violation(
            "LABOR-STOP-REQ-001",
            "Labor entry is already stopped",
        )
    if labor.user_id == actor_id:
        raise _rule_violation(
            "LABOR-STOP-REQ-003",
            "Cannot request stop on your own labor entry",
        )

    # Route-layer resolve_version_assertion already guarantees int.
    assert data.labor_entry_version is not None, (
        "route must resolve version before service"
    )
    try:
        check_version(
            labor, data.labor_entry_version, entity_type="LaborEntry",
        )
    except VersionConflictError as e:
        from forge.schemas.labor import LaborOut
        raise version_conflict_response(
            e,
            current_entity=LaborOut.model_validate(labor).model_dump(
                mode="json",
            ),
        ) from e

    stop_request = StopRequest(
        labor_entry_id=labor.id,
        requested_by_user_id=actor_id,
        note=data.note,
        status=StopRequestStatus.pending,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(stop_request)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Partial-unique index violation: another pending stop-
        # request already exists for this entry.  Spec
        # LABOR-STOP-REQ-002 is the canonical error.
        await session.rollback()
        raise _rule_violation(
            "LABOR-STOP-REQ-002",
            "A pending stop-request already exists for this labor entry",
        ) from exc

    display_name = await _resolve_requester_display_name(session, actor_id)

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role="operator",
        action_type="labor.stop_requested",
        entity_type="stop_request",
        entity_id=stop_request.id,
        before_state=None,
        after_state={
            "labor_entry_id": str(labor.id),
            "target_user_id": str(labor.user_id),
            "note": data.note,
        },
    )

    return StopRequestOut(
        request_id=stop_request.id,
        labor_entry_id=labor.id,
        status=StopRequestStatus.pending,
        requested_by=StopRequestActor(
            user_id=actor_id, display_name=display_name,
        ),
        note=data.note,
        created_at=stop_request.created_at,
    )


# ---------------------------------------------------------------------------
# Flag correction (operator)
# ---------------------------------------------------------------------------


async def apply_flag_correction(
    session: AsyncSession,
    labor_id: _uuid.UUID,
    data: FlagCorrectionIn,
    actor_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> None:
    """Operator flags a labor entry for engineer review (§5.4.5).

    Narrowly scoped: NO state transition, NO version check, NO
    persisted flag row — the spec calls for an audit-visible
    attention signal only.  The engineer sees flagged entries
    on their next poll of the Work Order view (§5.4.5 language:
    "flagged entries appear in the engineer's work order view
    as a pending item"); the read surface that renders those
    flagged entries is tracked separately — the audit row is
    the durable signal regardless.

    Invariants:
      1. Entry exists, program scope (404 on cross-program).
      2. Caller cannot flag their OWN entry — flagging one's own
         entry for engineer review is nonsensical (operator would
         just edit their own entry via labor:update_own).  Not a
         spec-named error code; reuse RULE_VIOLATION with a
         descriptive message.
    """
    labor = await _get_labor_entry(session, labor_id)
    await _enforce_labor_program_scope(session, labor, program_codes)

    if labor.user_id == actor_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RULE_VIOLATION",
                "message": (
                    "Cannot flag your own labor entry for correction; "
                    "edit it directly via labor:update_own."
                ),
            },
        )

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role="operator",
        action_type="labor.correction_requested",
        entity_type="labor_entry",
        entity_id=labor.id,
        before_state=None,
        after_state={
            "target_user_id": str(labor.user_id),
            "note": data.note,
        },
    )


# ---------------------------------------------------------------------------
# Operation-detail read-model: pending stop-requests for an operation
# ---------------------------------------------------------------------------


async def pending_stop_requests_for_operation(
    session: AsyncSession, op_id: _uuid.UUID,
) -> list[StopRequestOut]:
    """Return all PENDING stop-requests on an op's active labor entries.

    Used by operation_service.get_operation_detail to fill the
    §5.4.8 `stop_requests` array.  Only pending rows — fulfilled
    and expired entries are surfaced via audit trail per spec.

    Two-step: fetch the stop-request rows joined to labor entries
    for this op, then resolve display_name for each requester.  The
    display-name resolution is batched via a single IN-query to
    avoid N+1 on operations with many pending requests.
    """
    result = await session.execute(
        select(StopRequest)
        .join(LaborEntry, StopRequest.labor_entry_id == LaborEntry.id)
        .where(
            LaborEntry.operation_id == op_id,
            StopRequest.status == StopRequestStatus.pending,
        )
        .order_by(StopRequest.created_at.asc()),
    )
    rows = list(result.scalars().all())
    if not rows:
        return []

    user_ids = {r.requested_by_user_id for r in rows}
    user_result = await session.execute(
        select(User.id, User.display_name).where(User.id.in_(user_ids)),
    )
    name_map = {uid: name for uid, name in user_result.all()}

    return [
        StopRequestOut(
            request_id=r.id,
            labor_entry_id=r.labor_entry_id,
            status=r.status,
            requested_by=StopRequestActor(
                user_id=r.requested_by_user_id,
                display_name=name_map.get(r.requested_by_user_id, ""),
            ),
            note=r.note,
            created_at=r.created_at,
        )
        for r in rows
    ]
