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
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, TraceSourceType
from forge.models.part import PartInstance, TraceabilityLink
from forge.models.program import Program
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


async def update_part(
    session: AsyncSession,
    part_id: _uuid.UUID,
    data: PartUpdate,
    user_id: _uuid.UUID,
    program_codes: list[str] | None = None,
) -> PartInstance:
    """Update a part's status / location / parent assembly.

    State leaving: part.status.  State entering: data.status (if given).
    Invariants (in order):
      1. Caller has program scope.
      2. Optimistic version matches.
      3. Status transition (if requested) is legal per
         VALID_PART_TRANSITIONS.
    """
    part = await _load_part_in_scope(session, part_id, program_codes)

    try:
        check_version(part, data.version, entity_type="PartInstance")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    before_status = part.status.value
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

    part.updated_by = user_id
    increment_version(part)
    await session.flush()

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
    """Load a part in scope (used by tests and traceability)."""
    return await _load_part_in_scope(session, part_id, program_codes)


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
