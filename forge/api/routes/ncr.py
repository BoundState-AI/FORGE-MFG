#!/usr/bin/env python3
"""
Module:   NCR Routes
Purpose:  HTTP endpoints for the NCR lifecycle (spec §5.5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md build philosophy.  All workflow gates,
  SoD enforcement, version checks, and program-scope filtering live in
  ncr_service so the route layer cannot be the source of a bypass.
  Each route declares the spec §5.0.2 permission it requires and
  forwards `auth.programs` to the service for §5.0.3 enforcement.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, get_auth_context, require_permission
from forge.api.deps import get_db
from forge.schemas.ncr import (
    NCRClose,
    NCRCreate,
    NCRDisposition,
    NCROut,
    NCRPatch,
)
from forge.services import ncr_service as ncr_svc

router = APIRouter(prefix="/api/ncrs", tags=["ncrs"])


@router.post("", response_model=NCROut, status_code=201)
async def create_ncr(
    data: NCRCreate,
    auth: AuthContext = Depends(require_permission("ncr:create")),
    session: AsyncSession = Depends(get_db),
) -> NCROut:
    """Create a new NCR in `open` status."""
    ncr = await ncr_svc.create_ncr(
        session, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(ncr)
    return NCROut.model_validate(ncr)


@router.get("", response_model=dict[str, object])
async def list_ncrs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("ncr:read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List NCRs (paginated, program-scoped)."""
    items, total = await ncr_svc.list_ncrs(
        session, program_codes=auth.programs,
        page=page, page_size=page_size,
    )
    return {
        "items": [NCROut.model_validate(n) for n in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/{ncr_id}", response_model=NCROut)
async def get_ncr(
    ncr_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("ncr:read")),
    session: AsyncSession = Depends(get_db),
) -> NCROut:
    """Get an NCR (program-scoped via parent WO)."""
    ncr = await ncr_svc.get_ncr(
        session, ncr_id, program_codes=auth.programs
    )
    return NCROut.model_validate(ncr)


@router.patch("/{ncr_id}/disposition", response_model=NCROut)
async def disposition_ncr(
    ncr_id: uuid.UUID,
    data: NCRDisposition,
    auth: AuthContext = Depends(require_permission("ncr:disposition")),
    session: AsyncSession = Depends(get_db),
) -> NCROut:
    """Disposition an NCR (open → dispositioned).

    SOD-002 (spec §4.2): the initiator cannot disposition the same NCR.
    Enforcement happens in the service layer.
    """
    ncr = await ncr_svc.disposition_ncr(
        session, ncr_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(ncr)
    return NCROut.model_validate(ncr)


@router.patch("/{ncr_id}", response_model=NCROut)
async def patch_ncr(
    ncr_id: uuid.UUID,
    data: NCRPatch,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> NCROut:
    """Replace evidence_refs on an NCR (build spec v1.4.2 Decision 1).

    Permissions: ncr:disposition OR ncr:close — either role is
    authorized to attach completion evidence during the remediation
    cycle.  Since a FastAPI dep cannot OR two permissions, the
    check is inline here.  Any other NCR-edit surface stays off
    this route; broader NCR edits are a separate slice.
    """
    if not (
        auth.has_permission("ncr:disposition")
        or auth.has_permission("ncr:close")
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Missing required permission: ncr:disposition "
                "or ncr:close"
            ),
        )
    ncr = await ncr_svc.patch_ncr(
        session, ncr_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(ncr)
    return NCROut.model_validate(ncr)


@router.post("/{ncr_id}/close", response_model=NCROut)
async def close_ncr(
    ncr_id: uuid.UUID,
    data: NCRClose,
    auth: AuthContext = Depends(require_permission("ncr:close")),
    session: AsyncSession = Depends(get_db),
) -> NCROut:
    """Close an NCR (dispositioned → closed)."""
    ncr = await ncr_svc.close_ncr(
        session, ncr_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(ncr)
    return NCROut.model_validate(ncr)
