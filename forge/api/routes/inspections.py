#!/usr/bin/env python3
"""
Module:   Inspection Routes
Purpose:  HTTP endpoints for recording inspections and sign-offs on
          operations (spec §5.4).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md build philosophy.  All SoD enforcement
  and quality-gate validation lives in inspection_service so a future
  caller cannot bypass the gate by skipping the route layer.  Routes
  declare the spec §5.0.2 permission required and pass auth.programs
  through to the service for spec §5.0.3 program-scope filtering.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.schemas.inspection import (
    InspectionCreate,
    InspectionOut,
    SignOffCreate,
    SignOffOut,
)
from forge.services import inspection_service as insp_svc

router = APIRouter(tags=["inspections"])


@router.post(
    "/api/operations/{op_id}/inspect",
    response_model=InspectionOut,
    status_code=201,
)
async def record_inspection(
    op_id: uuid.UUID,
    data: InspectionCreate,
    auth: AuthContext = Depends(require_permission("inspect:create")),
    session: AsyncSession = Depends(get_db),
) -> InspectionOut:
    """Record an inspection event (SOD-001 enforced in service)."""
    event = await insp_svc.record_inspection(
        session, op_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(event)
    return InspectionOut.model_validate(event)


@router.post(
    "/api/operations/{op_id}/sign-off",
    response_model=SignOffOut,
    status_code=201,
)
async def record_sign_off(
    op_id: uuid.UUID,
    data: SignOffCreate,
    auth: AuthContext = Depends(require_permission("inspect:sign_off")),
    session: AsyncSession = Depends(get_db),
) -> SignOffOut:
    """Record a sign-off event (SOD-005 enforced in service)."""
    event = await insp_svc.record_sign_off(
        session, op_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(event)
    return SignOffOut.model_validate(event)
