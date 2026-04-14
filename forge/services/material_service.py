#!/usr/bin/env python3
"""
Module:   Material Service
Purpose:  Business logic for the MaterialCertification lifecycle —
          receive, accept (SOD-003), and atomically consume against the
          lot balance.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin; every gate lives here so a future caller cannot
  skip SOD-003 (receiver != accepter) or the atomic-balance check by
  writing directly to the ORM.  The authoritative balance enforcement
  is `SELECT ... FOR UPDATE` per spec §4.4 COMP-005 — the advisory
  rule in the rules engine exists for pre-check UX, but the service
  must perform the transactional check regardless of the rule's result.

  Program scope is enforced by comparing the certification's program
  code against `auth.programs` at the service entrypoint.  Wildcard
  "*" is local-dev only.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, CertStatus
from forge.models.material import MaterialCertification, MaterialConsumption
from forge.models.program import Program
from forge.rules.separation_of_duties import (
    SoDViolation,
    check_sod_003_material_acceptance,
)
from forge.rules.status_transitions import validate_cert_transition
from forge.schemas.material import (
    MaterialAccept,
    MaterialConsume,
    MaterialReceive,
)
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


def _violation_response(violation: SoDViolation) -> HTTPException:
    """Convert an SoD violation to the §5.15 422 RULE_VIOLATION envelope."""
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


async def _program_code_for_id(
    session: AsyncSession, program_id: _uuid.UUID,
) -> str:
    """Return the program code for a given program_id, or raise 404.

    Spec §5.0.3: program-scope filtering is enforced at the service
    layer.  We need the code (not the id) because the auth context
    carries `programs: list[str]` of codes.
    """
    result = await session.execute(
        select(Program.code).where(Program.id == program_id)
    )
    code = result.scalar_one_or_none()
    if code is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return code


def _require_program_access(
    program_code: str, program_codes: list[str] | None,
) -> None:
    """Raise 404 if the caller is not in the required program.

    UX spec v1.5.1 §3.2 / §6.7.4: cross-program access returns 404,
    not 403, to prevent existence leaks.  The detail message is kept
    generic for the same reason — even the message would otherwise
    confirm that an entity exists in a program the caller cannot see.
    """
    if program_codes is None:
        return
    if "*" in program_codes:
        return
    if program_code not in program_codes:
        raise HTTPException(
            status_code=404,
            detail="Material certification not found",
        )


# ---------------------------------------------------------------------------
# Receive
# ---------------------------------------------------------------------------


async def receive_material(
    session: AsyncSession,
    data: MaterialReceive,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> MaterialCertification:
    """Receive a new material lot.

    State leaving: (none).  State entering: quarantine.
    Invariants:
      1. Caller has program scope on the destination program.
      2. Lot is created in `quarantine` (SOD-003 requires acceptance
         by a second party before the lot is usable).
      3. quantity_remaining starts equal to quantity_received.
      4. received_by_id is the caller (SOD-003 anchor).
    """
    program_code = await _program_code_for_id(session, data.program_id)
    _require_program_access(program_code, program_codes)

    now = datetime.now(UTC)
    cert = MaterialCertification(
        program_id=data.program_id,
        material_spec=data.material_spec,
        lot_number=data.lot_number,
        heat_number=data.heat_number,
        supplier=data.supplier,
        cert_document_ref_id=data.cert_document_ref_id,
        received_at=now,
        received_by_id=user_id,
        shelf_life_expiry=data.shelf_life_expiry,
        status=CertStatus.quarantine,
        quantity_received=data.quantity_received,
        quantity_remaining=data.quantity_received,
        unit_of_measure=data.unit_of_measure,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(cert)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="material.receive",
        entity_type="material_certification",
        entity_id=cert.id,
        after_state={
            "status": cert.status.value,
            "lot_number": cert.lot_number,
            "quantity_received": cert.quantity_received,
        },
    )
    return cert


# ---------------------------------------------------------------------------
# Accept  (SOD-003)
# ---------------------------------------------------------------------------


async def _load_cert_in_scope(
    session: AsyncSession,
    cert_id: _uuid.UUID,
    program_codes: list[str] | None,
) -> MaterialCertification:
    """Load a cert by id, enforcing program scope.

    Raises 404 if missing, 403 if caller is outside the cert's program.
    """
    cert = await session.get(MaterialCertification, cert_id)
    if cert is None:
        raise HTTPException(
            status_code=404, detail="Material certification not found"
        )
    code = await _program_code_for_id(session, cert.program_id)
    _require_program_access(code, program_codes)
    return cert


async def accept_material(
    session: AsyncSession,
    cert_id: _uuid.UUID,
    data: MaterialAccept,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> MaterialCertification:
    """Accept a quarantined material lot.

    State leaving: quarantine.  State entering: accepted.
    Invariants (in order):
      1. Caller has program scope.
      2. Optimistic version matches.
      3. Transition quarantine → accepted is legal per §3.1.
      4. SOD-003: the receiver cannot also be the accepter.
    """
    cert = await _load_cert_in_scope(session, cert_id, program_codes)

    try:
        check_version(cert, data.version, entity_type="MaterialCertification")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if not validate_cert_transition(cert.status, CertStatus.accepted):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid transition: {cert.status.value} → "
                f"{CertStatus.accepted.value}"
            ),
        )

    sod_violation = check_sod_003_material_acceptance(
        receiver_id=cert.received_by_id, accepter_id=user_id
    )
    if sod_violation is not None:
        raise _violation_response(sod_violation)

    before = cert.status.value
    cert.status = CertStatus.accepted
    cert.accepted_by_id = user_id
    cert.accepted_at = datetime.now(UTC)
    cert.updated_by = user_id
    increment_version(cert)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="material.accept",
        entity_type="material_certification",
        entity_id=cert.id,
        before_state={"status": before},
        after_state={"status": cert.status.value},
    )
    return cert


# ---------------------------------------------------------------------------
# Consume  (atomic balance; spec §4.4 COMP-005)
# ---------------------------------------------------------------------------


async def _require_op_under_wo(
    session: AsyncSession,
    op_id: _uuid.UUID,
    wo_id: _uuid.UUID,
) -> None:
    """Verify the operation belongs to the declared work order.

    Why this gate: the consume request carries both work_order_id and
    operation_id.  Without this check, a caller could submit a valid
    op_id from a DIFFERENT WO — the consumption row would record
    `work_order_id=A` while the op actually lives under WO B, breaking
    traceability.  Fail closed with 422.
    """
    from forge.models.operation import Operation

    op = await session.get(Operation, op_id)
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    if op.work_order_id != wo_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "Operation does not belong to the provided work order"
            ),
        )


def _require_wo_cert_program_match(
    wo: Any, cert: MaterialCertification,
) -> None:
    """Defense-in-depth: WO.program_id must equal cert.program_id.

    The caller's program scope already protects the WO and the cert
    individually, but a caller with access to BOTH programs could still
    misroute a consumption across programs.  This explicit equality
    closes that gap.  Any of the following trigger a 422:
      - cross-program consumption (WO in program X, cert in program Y)
      - stale / swapped routing by the client
    """
    if wo.program_id != cert.program_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "Work order program does not match the material "
                "certification's program"
            ),
        )


def _require_consumable(cert: MaterialCertification) -> None:
    """Gate consumption on status + shelf life (spec §4.4 COMP-002).

    Invariants:
      - status must be `accepted`.
      - shelf_life_expiry (if set) must not be past.

    A rejected, quarantined, consumed, or expired cert is never a
    valid consumption source — this fails closed.
    """
    if cert.status != CertStatus.accepted:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Material not in accepted status (current: {cert.status.value})"
            ),
        )
    if (
        cert.shelf_life_expiry is not None
        and cert.shelf_life_expiry <= datetime.now(UTC)
    ):
        raise HTTPException(
            status_code=422,
            detail="Material past shelf-life expiry",
        )


async def consume_material(
    session: AsyncSession,
    cert_id: _uuid.UUID,
    data: MaterialConsume,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> MaterialConsumption:
    """Atomically consume material from an accepted lot.

    State leaving: accepted.  State entering: accepted (unchanged —
    consumption decrements quantity_remaining; the cert transitions
    to `consumed` only when a future administrative gate zeros it).
    Invariants (in order):
      1. Caller has program scope on the cert.
      2. Parent work order is in the caller's program scope.
      3. operation_id belongs to the provided work_order_id (a rogue
         op_id from another WO cannot charge consumption against this
         WO's audit trail).
      4. WO's program_id equals the cert's program_id (defense in
         depth — the scope check in (1) and (2) already narrows the
         identity space, but the explicit equality closes the gap
         where both the WO and the cert are within the caller's
         programs yet belong to different programs).
      5. Row-level lock via SELECT ... FOR UPDATE serializes concurrent
         consumers against this lot (spec §4.4 COMP-005 authoritative
         check — the advisory rule is not sufficient alone).
      6. Post-lock: optimistic version matches — concurrent consumers
         that lose the race get a clean 409 instead of lost-update
         silently.
      7. Post-lock: status is accepted and not expired.
      8. Post-lock: quantity_remaining >= quantity.  If not, 422.
    """
    # Initial scope checks (before locking)
    cert_preview = await _load_cert_in_scope(session, cert_id, program_codes)
    wo = await get_work_order(
        session, data.work_order_id, program_codes=program_codes
    )
    await _require_op_under_wo(session, data.operation_id, data.work_order_id)
    _require_wo_cert_program_match(wo, cert_preview)

    # Row-level lock acquires the cert row; concurrent consumers block
    # here until this txn commits/rolls back.
    locked = await session.execute(
        select(MaterialCertification)
        .where(MaterialCertification.id == cert_id)
        .with_for_update()
    )
    cert = locked.scalar_one()

    # Optimistic version check runs after the lock so the check sees
    # the same row contents other invariants check against.
    try:
        check_version(cert, data.version, entity_type="MaterialCertification")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    _require_consumable(cert)

    if data.quantity > cert.quantity_remaining:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Insufficient quantity: requested {data.quantity}, "
                f"remaining {cert.quantity_remaining}"
            ),
        )

    cert.quantity_remaining -= data.quantity
    cert.updated_by = user_id
    increment_version(cert)

    now = datetime.now(UTC)
    consumption = MaterialConsumption(
        certification_id=cert.id,
        work_order_id=data.work_order_id,
        operation_id=data.operation_id,
        part_instance_id=data.part_instance_id,
        quantity=data.quantity,
        unit_of_measure=data.unit_of_measure,
        issued_by_id=user_id,
        issued_at=now,
        lot_balance_after=cert.quantity_remaining,
        notes=data.notes,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(consumption)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="material.consume",
        entity_type="material_consumption",
        entity_id=consumption.id,
        after_state={
            "certification_id": str(cert.id),
            "quantity": data.quantity,
            "lot_balance_after": cert.quantity_remaining,
        },
    )
    return consumption


# ---------------------------------------------------------------------------
# Read helpers (used by tests + future endpoints)
# ---------------------------------------------------------------------------


async def get_material_cert(
    session: AsyncSession,
    cert_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> MaterialCertification:
    """Load a cert in the caller's program scope (for admin / test inspection)."""
    return await _load_cert_in_scope(session, cert_id, program_codes)


async def list_material_certs(
    session: AsyncSession,
    *,
    program_codes: list[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[MaterialCertification], int]:
    """List certs in the caller's programs, paginated.

    Program scope is enforced by joining Program and filtering on code.
    """
    base_query = select(MaterialCertification)
    count_query = select(func.count(MaterialCertification.id))

    if program_codes is not None and "*" not in program_codes:
        base_query = base_query.join(
            Program, Program.id == MaterialCertification.program_id
        ).where(Program.code.in_(program_codes))
        count_query = count_query.join(
            Program, Program.id == MaterialCertification.program_id
        ).where(Program.code.in_(program_codes))

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(
        base_query
        .order_by(MaterialCertification.received_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    return list(result.scalars().all()), total
