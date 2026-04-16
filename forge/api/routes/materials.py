#!/usr/bin/env python3
"""
Module:   Material Routes
Purpose:  HTTP endpoints for material receive / accept / consume (spec §5.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md.  SOD-003, version checks, atomic
  balance (SELECT FOR UPDATE), and shelf-life enforcement all live in
  material_service.  Each route declares its §5.0.2 permission and
  forwards auth.programs to the service for §5.0.3 enforcement.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.models.enums import CertStatus
from forge.schemas.material import (
    MaterialAccept,
    MaterialCertOut,
    MaterialConsume,
    MaterialConsumptionOut,
    MaterialReceive,
)
from forge.services import material_service as mat_svc

router = APIRouter(prefix="/api/material-certs", tags=["materials"])


@router.get("", response_model=dict[str, object])
async def list_material_certs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: CertStatus | None = Query(default=None),
    auth: AuthContext = Depends(require_permission("mat:read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List material certifications (paginated, program-scoped).

    UX spec v1.5.1 §5.7 Material Certification List: status filter
    (quarantine / accepted / rejected / consumed / expired / scrapped)
    and "All (assigned programs)" multi-program read per §6.7.1.
    Parity patch closes the gap between Build Spec §5.6 (write routes
    only) and the UX spec's requirement for a real list surface.
    """
    items, total = await mat_svc.list_material_certs(
        session,
        program_codes=auth.programs,
        page=page,
        page_size=page_size,
        status=status,
    )
    return {
        "items": [MaterialCertOut.model_validate(c) for c in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.post("", response_model=MaterialCertOut, status_code=201)
async def receive_material(
    data: MaterialReceive,
    auth: AuthContext = Depends(require_permission("mat:receive")),
    session: AsyncSession = Depends(get_db),
) -> MaterialCertOut:
    """Receive a material lot into quarantine."""
    cert = await mat_svc.receive_material(
        session, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(cert)
    return MaterialCertOut.model_validate(cert)


@router.patch("/{cert_id}/accept", response_model=MaterialCertOut)
async def accept_material(
    cert_id: uuid.UUID,
    data: MaterialAccept,
    auth: AuthContext = Depends(require_permission("mat:accept")),
    session: AsyncSession = Depends(get_db),
) -> MaterialCertOut:
    """Accept a quarantined lot (SOD-003 enforced in service)."""
    cert = await mat_svc.accept_material(
        session, cert_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(cert)
    return MaterialCertOut.model_validate(cert)


@router.post(
    "/{cert_id}/consume",
    response_model=MaterialConsumptionOut,
    status_code=201,
)
async def consume_material(
    cert_id: uuid.UUID,
    data: MaterialConsume,
    auth: AuthContext = Depends(require_permission("mat:consume")),
    session: AsyncSession = Depends(get_db),
) -> MaterialConsumptionOut:
    """Atomically consume material (SELECT FOR UPDATE in service)."""
    consumption = await mat_svc.consume_material(
        session, cert_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(consumption)
    return MaterialConsumptionOut.model_validate(consumption)
