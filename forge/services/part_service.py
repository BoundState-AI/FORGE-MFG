#!/usr/bin/env python3
"""
Module:   Part Service
Purpose:  Business logic for PartInstance CRUD and traceability chain
          assembly (spec §5.7).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin; status transitions are gated here via
  VALID_PART_TRANSITIONS.  Program scope is enforced at the service
  entrypoint by comparing the part's program_id to the caller's
  `auth.programs` codes (wildcard "*" bypasses for local dev).  The
  traceability chain is assembled in a dedicated helper so the shape
  is deterministic and the walk-depth is bounded (defensive against
  pathological self-reference loops in the data).
"""

from __future__ import annotations

import uuid as _uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, PartStatus, TraceSourceType
from forge.models.ncr import NonconformanceReport
from forge.models.part import PartInstance, TraceabilityLink
from forge.models.program import Program
from forge.models.work_order import WorkOrder
from forge.rules.status_transitions import validate_part_transition
from forge.schemas.part import PartCreate, PartUpdate
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)

# Safety bound for the assembly-chain walk.  A real engine has
# ~3-5 levels of assembly; 32 is 10x headroom and guarantees we do
# not loop indefinitely on corrupted data.
_ASSEMBLY_CHAIN_MAX_DEPTH = 32


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------


async def _program_code_for_id(
    session: AsyncSession, program_id: _uuid.UUID,
) -> str:
    """Return the program code for a given program_id, or 404."""
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
    """Raise 404 if caller is not in the required program.

    UX spec v1.5.1 §3.2 / §6.7.4: cross-program access returns 404,
    not 403, to prevent existence leaks.  The detail message is kept
    generic — even the message would otherwise confirm that an entity
    exists in a program the caller cannot see.
    """
    if program_codes is None:
        return
    if "*" in program_codes:
        return
    if program_code not in program_codes:
        raise HTTPException(status_code=404, detail="Part not found")


async def _load_part_in_scope(
    session: AsyncSession,
    part_id: _uuid.UUID,
    program_codes: list[str] | None,
) -> PartInstance:
    """Load a part by id with program-scope enforcement."""
    part = await session.get(PartInstance, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    code = await _program_code_for_id(session, part.program_id)
    _require_program_access(code, program_codes)
    return part


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_part(
    session: AsyncSession,
    data: PartCreate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> PartInstance:
    """Create a new PartInstance in `raw` status.

    State leaving: (none).  State entering: raw.
    Invariants:
      1. Caller has program scope for the destination program.
      2. serial_number is unique (DB constraint backstop).
      3. Status starts at `raw` per spec §3.1.
    """
    program_code = await _program_code_for_id(session, data.program_id)
    _require_program_access(program_code, program_codes)

    from forge.models.enums import PartStatus

    part = PartInstance(
        serial_number=data.serial_number,
        part_number=data.part_number,
        part_description=data.part_description,
        program_id=data.program_id,
        status=PartStatus.raw,
        material_certification_id=data.material_certification_id,
        parent_assembly_id=data.parent_assembly_id,
        current_location=data.current_location,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(part)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="part.create",
        entity_type="part_instance",
        entity_id=part.id,
        after_state={
            "serial_number": part.serial_number,
            "status": part.status.value,
        },
    )
    return part


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def _resolve_scrap_ncr(
    session: AsyncSession,
    part: PartInstance,
    reason_ncr_id: _uuid.UUID,
    program_codes: list[str] | None,
) -> NonconformanceReport:
    """Validate the reason_ncr referenced by a scrap transition.

    Invariants (UX spec v1.5.1 §5.6.5):
      1. NCR exists (404 otherwise via the scope check — §3.2 leak rule
         deliberately conflates "no such NCR" with "NCR in another
         program").
      2. NCR's parent WO is in the caller's program scope.  WO program
         must equal the part's program — the scrap flow only links an
         NCR to a part within the same program; cross-program linkage
         would let a QA manager in program A stamp a scrap on a part
         in program B purely by referencing an NCR.
      3. Linkage is truthful: either NCR.part_instance_id == part.id
         (the NCR names this part directly) OR the NCR is untied at
         the part level (part_instance_id is NULL).  The second clause
         covers the "scrap material / scrap assembly" flow where an
         NCR was raised against the operation or WO, not the serialized
         part — that is the exact case §5.6.5 is built for.
    """
    ncr = await session.get(NonconformanceReport, reason_ncr_id)
    if ncr is None:
        raise HTTPException(
            status_code=422,
            detail="reason_ncr_id does not reference an existing NCR",
        )
    # Scope gate — program must match the part.  The WO carries the
    # canonical program association; reuse _program_code_for_id so the
    # §3.2 existence-leak posture stays consistent with part lookup.
    wo = await session.get(WorkOrder, ncr.work_order_id)
    if wo is None:
        raise HTTPException(
            status_code=422,
            detail="reason_ncr_id points at an NCR with no parent WO",
        )
    ncr_program_code = await _program_code_for_id(session, wo.program_id)
    _require_program_access(ncr_program_code, program_codes)
    if wo.program_id != part.program_id:
        raise HTTPException(
            status_code=422,
            detail="reason_ncr_id is not in the same program as the part",
        )
    # Relationship gate — explicit link or part-untied NCR only.
    if (
        ncr.part_instance_id is not None
        and ncr.part_instance_id != part.id
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "reason_ncr_id names a different part instance; scrap "
                "linkage must reference the part being scrapped"
            ),
        )
    return ncr


async def update_part(
    session: AsyncSession,
    part_id: _uuid.UUID,
    data: PartUpdate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
    has_scrap_permission: bool = False,
) -> PartInstance:
    """Update a part's status / location / parent assembly.

    State leaving: part.status.  State entering: data.status (if given).
    Invariants (in order):
      1. Caller has program scope.
      2. Optimistic version matches.
      3. Status transition (if requested) is legal per
         VALID_PART_TRANSITIONS.
      4. Scrap-specific gates (only when data.status == scrapped):
         a. Caller carries the part:scrap permission.  Gated here as
            a second line of defense — the route layer requires the
            permission too, but this check means a future internal
            caller cannot escape scrap authority by bypassing the route.
         b. reason_ncr_id is present.  UX spec §5.6.5 wire shape is
            {status: "scrapped", reason_ncr_id: <ncr_id>}; the only
            legitimate status=scrapped call carries the link.
         c. reason_ncr is validated by _resolve_scrap_ncr (scope +
            program match + part-instance relationship).

    Audit footprint on scrap success:
      - part.status_changed  (spec §6.6.1 vocabulary for part lifecycle)
      - ncr.scrap_linkage_confirmed  (spec §5.6.5 explicit event name)
    Non-scrap updates keep the pre-existing part.update audit record
    so downstream audit consumers that index on that action_type do
    not silently lose events.
    """
    part = await _load_part_in_scope(session, part_id, program_codes)

    try:
        check_version(part, data.version, entity_type="PartInstance")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    before_status = part.status.value
    is_scrap_transition = (
        data.status is not None
        and data.status != part.status
        and data.status == PartStatus.scrapped
    )

    if is_scrap_transition:
        if not has_scrap_permission:
            raise HTTPException(
                status_code=403,
                detail="Scrap transition requires the part:scrap permission",
            )
        if data.reason_ncr_id is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "reason_ncr_id is required when transitioning a part "
                    "to scrapped"
                ),
            )
        await _resolve_scrap_ncr(
            session, part, data.reason_ncr_id, program_codes
        )

    if data.status is not None and data.status != part.status:
        if not validate_part_transition(part.status, data.status):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid transition: {part.status.value} → "
                    f"{data.status.value}"
                ),
            )
        part.status = data.status

    if data.current_location is not None:
        part.current_location = data.current_location
    if data.parent_assembly_id is not None:
        part.parent_assembly_id = data.parent_assembly_id
    if is_scrap_transition:
        part.reason_ncr_id = data.reason_ncr_id

    part.updated_by = user_id
    increment_version(part)
    await session.flush()

    if is_scrap_transition:
        # mypy narrows away the Optional after the earlier
        # is_scrap_transition gate validated reason_ncr_id is present.
        assert data.reason_ncr_id is not None
        # Spec §6.6.1 vocabulary: part.status_changed is the named
        # lifecycle event.  The legacy part.update event is skipped
        # here to avoid double-counting the same transition on the
        # audit timeline — downstream consumers that filter on the
        # spec-named event will see a single authoritative row.
        await create_audit_record(
            session,
            event_id=_uuid.uuid4(),
            actor_type=ActorType.human,
            user_id=user_id,
            user_role=None,
            action_type="part.status_changed",
            entity_type="part_instance",
            entity_id=part.id,
            before_state={"status": before_status},
            after_state={
                "status": part.status.value,
                "reason_ncr_id": str(data.reason_ncr_id),
            },
        )
        await create_audit_record(
            session,
            event_id=_uuid.uuid4(),
            actor_type=ActorType.human,
            user_id=user_id,
            user_role=None,
            action_type="ncr.scrap_linkage_confirmed",
            entity_type="ncr",
            entity_id=data.reason_ncr_id,
            after_state={
                "part_instance_id": str(part.id),
                "serial_number": part.serial_number,
            },
        )
    else:
        await create_audit_record(
            session,
            event_id=_uuid.uuid4(),
            actor_type=ActorType.human,
            user_id=user_id,
            user_role=None,
            action_type="part.update",
            entity_type="part_instance",
            entity_id=part.id,
            before_state={"status": before_status},
            after_state={"status": part.status.value},
        )
    return part


# ---------------------------------------------------------------------------
# Read + Traceability
# ---------------------------------------------------------------------------


async def get_part(
    session: AsyncSession,
    part_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> PartInstance:
    """Load a part with program-scope enforcement."""
    return await _load_part_in_scope(session, part_id, program_codes)


async def list_parts(
    session: AsyncSession,
    *,
    program_codes: list[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[PartInstance], int]:
    """List parts in the caller's programs, paginated.

    UX spec v1.5.1 §5.8 Part List surface (implied by the §3.2 URL
    table ``/parts`` and the §6.7.1 "All (assigned programs)" list set
    including Part List).  Parity patch adds the read endpoint that
    Build Spec §5.7 previously left out — the service scope pattern
    mirrors ``list_material_certs`` exactly so both lists behave the
    same under ``X-Program-Id: *``.

    Program scope is enforced by joining Program on ``code``, matching
    the existing ``_load_part_in_scope`` pattern.  Wildcard ``"*"`` in
    the caller's claim bypasses the membership filter (local-dev
    parity with other list endpoints).
    """
    base_query = select(PartInstance)
    count_query = select(func.count(PartInstance.id))

    if program_codes is not None and "*" not in program_codes:
        base_query = base_query.join(
            Program, Program.id == PartInstance.program_id
        ).where(Program.code.in_(program_codes))
        count_query = count_query.join(
            Program, Program.id == PartInstance.program_id
        ).where(Program.code.in_(program_codes))

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(
        base_query
        .order_by(PartInstance.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def _walk_assembly_chain(
    session: AsyncSession, start: PartInstance,
) -> list[PartInstance]:
    """Walk parent_assembly_id upward up to the bounded depth.

    Why bounded: defense against corrupted self-referential data.  A
    chain deeper than 32 assemblies is not physically realistic and
    indicates a cycle — returning what we have rather than looping
    forever is the safer failure mode.
    """
    chain: list[PartInstance] = []
    current = start
    for _ in range(_ASSEMBLY_CHAIN_MAX_DEPTH):
        if current.parent_assembly_id is None:
            break
        parent = await session.get(PartInstance, current.parent_assembly_id)
        if parent is None or parent.id == start.id:
            break
        chain.append(parent)
        current = parent
    return chain


async def _load_child_parts(
    session: AsyncSession, parent_id: _uuid.UUID,
) -> list[PartInstance]:
    """All parts whose parent_assembly_id equals this part."""
    result = await session.execute(
        select(PartInstance).where(
            PartInstance.parent_assembly_id == parent_id
        )
    )
    return list(result.scalars().all())


async def _load_trace_links(
    session: AsyncSession, part_id: _uuid.UUID,
) -> list[TraceabilityLink]:
    """Load all TraceabilityLink rows referencing this part (source or target).

    We filter by source_type/target_type == part_instance AND id match
    so a future entity id reuse (should never happen with UUIDs) cannot
    leak an unrelated link into the result.
    """
    result = await session.execute(
        select(TraceabilityLink).where(
            or_(
                (TraceabilityLink.source_type == TraceSourceType.part_instance)
                & (TraceabilityLink.source_id == part_id),
                (TraceabilityLink.target_type == TraceSourceType.part_instance)
                & (TraceabilityLink.target_id == part_id),
            )
        )
    )
    return list(result.scalars().all())


async def get_traceability(
    session: AsyncSession,
    part_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> tuple[
    PartInstance, list[PartInstance], list[PartInstance],
    list[TraceabilityLink],
]:
    """Return the full traceability chain for a part.

    Returns (part, assembly_chain_upward, child_parts, links).
    Program scope enforced via _load_part_in_scope.
    """
    part = await _load_part_in_scope(session, part_id, program_codes)
    assembly_chain = await _walk_assembly_chain(session, part)
    children = await _load_child_parts(session, part.id)
    links = await _load_trace_links(session, part.id)
    return part, assembly_chain, children, links
