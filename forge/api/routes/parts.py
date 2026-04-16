#!/usr/bin/env python3
"""
Module:   Part Routes
Purpose:  HTTP endpoints for parts CRUD and traceability (spec §5.7).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md.  Status transition gating, version
  checks, and program scope enforcement live in part_service.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.schemas.part import (
    PartCreate,
    PartOut,
    PartUpdate,
    TraceabilityOut,
    TraceLinkOut,
)
from forge.services import part_service as part_svc

router = APIRouter(prefix="/api/parts", tags=["parts"])


@router.get("", response_model=dict[str, object])
async def list_parts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("part:read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List parts (paginated, program-scoped).

    UX spec v1.5.1 §3.2 routes ``/parts`` to a Part Instance List
    surface and §6.7.1 includes Part List in the "All (assigned
    programs)" multi-program read set.  Parity patch closes the gap
    between Build Spec §5.7 (write-only + traceability) and the UX
    spec's list requirement.

    No server-side filters are added in this patch — spec §5.8 does
    not enumerate filter controls for the part list and the service
    stays minimal.  Clients that need client-side filters already
    have enough data in PartOut.
    """
    items, total = await part_svc.list_parts(
        session,
        program_codes=auth.programs,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [PartOut.model_validate(p) for p in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.post("", response_model=PartOut, status_code=201)
async def create_part(
    data: PartCreate,
    auth: AuthContext = Depends(require_permission("part:create")),
    session: AsyncSession = Depends(get_db),
) -> PartOut:
    """Create a serialized part instance in `raw` status."""
    part = await part_svc.create_part(
        session, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(part)
    return PartOut.model_validate(part)


@router.patch("/{part_id}", response_model=PartOut)
async def update_part(
    part_id: uuid.UUID,
    data: PartUpdate,
    auth: AuthContext = Depends(require_permission("part:update")),
    session: AsyncSession = Depends(get_db),
) -> PartOut:
    """Update a part (status/location/parent).  Transitions gated in service.

    Scrap transitions also require `part:scrap`.  UX spec v1.5.1
    §5.6.5 names qa_manager as the product-authorized scrap role;
    the admin role additionally carries part:scrap as a Phase-1
    break-glass lane (see ROLE_PERMISSIONS in forge.api.auth).  The
    route's base dependency is `part:update` so legitimate non-scrap
    edits from operator / engineer / qa_manager / admin all reach
    the service; the service enforces the scrap-specific permission
    itself when the requested status is `scrapped`.
    """
    part = await part_svc.update_part(
        session, part_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
        has_scrap_permission="part:scrap" in auth.permissions,
    )
    await session.commit()
    await session.refresh(part)
    return PartOut.model_validate(part)


@router.get("/{part_id}", response_model=PartOut)
async def get_part(
    part_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("part:read")),
    session: AsyncSession = Depends(get_db),
) -> PartOut:
    """Get a single part by ID (program-scoped).

    UX spec v1.5.1 §6.7.4 entity-detail authorization exception:
    cross-program access returns **404, not 403** — surfacing 403 would
    confirm the part exists in a program the caller cannot see.  The
    shape is intentionally indistinguishable from "no such record."
    ``part_service.get_part`` enforces that contract directly (via
    ``_load_part_in_scope``).

    Separate from ``/{part_id}/traceability``: the traceability
    endpoint carries the full assembly + child chain which is more
    expensive; the frontend part detail page wants to render the
    part header first, then fetch traceability in parallel.
    """
    part = await part_svc.get_part(
        session, part_id, program_codes=auth.programs,
    )
    return PartOut.model_validate(part)


@router.get("/{part_id}/traceability", response_model=TraceabilityOut)
async def get_traceability(
    part_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("part:read")),
    session: AsyncSession = Depends(get_db),
) -> TraceabilityOut:
    """Return the full traceability chain for a part."""
    part, assembly_chain, children, links = await part_svc.get_traceability(
        session, part_id, program_codes=auth.programs,
    )
    return TraceabilityOut(
        part=PartOut.model_validate(part),
        material_certification_id=part.material_certification_id,
        assembly_chain=[PartOut.model_validate(p) for p in assembly_chain],
        child_parts=[PartOut.model_validate(p) for p in children],
        links=[TraceLinkOut.model_validate(link) for link in links],
    )
