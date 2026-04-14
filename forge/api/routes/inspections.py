#!/usr/bin/env python3
"""
Module:   Inspection Routes
Purpose:  HTTP endpoints for the Inspection Queue read surface plus the
          existing record-inspection / sign-off write endpoints
          (UX spec v1.5.1 §5.4, §5.5.5).

BoundState AI 2026
Version: 0.2.0
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

  The queue endpoint is a READ surface: it requires `inspect:read`
  rather than `inspect:create`, so qa_manager drill-through
  (read-only per §5.5.5) works without granting write capability.
  Typed PaginatedResponse[InspectionQueueRowOut] gives the OpenAPI
  generator the precise item shape required by slice 5B-21.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.models.enums import InspectionQueueState
from forge.schemas.common import PaginatedResponse
from forge.schemas.inspection import (
    InspectionCreate,
    InspectionOut,
    InspectionQueueRowOut,
    SignOffCreate,
    SignOffOut,
)
from forge.services import inspection_service as insp_svc
from forge.services.inspection_service import InspectionQueueFilters

router = APIRouter(tags=["inspections"])


@router.get(
    "/api/inspections",
    response_model=PaginatedResponse[InspectionQueueRowOut],
)
async def list_inspection_queue(
    state: InspectionQueueState | None = Query(default=None),
    operation_id: uuid.UUID | None = Query(default=None),
    safety_critical: bool | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("inspect:read")),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[InspectionQueueRowOut]:
    """Paginated Inspection Queue (UX spec v1.5.1 §5.5.5).

    Drives the inspector worklist and the qa_manager read-only drill-
    through.  Rows carry server-derived `state`, `hold_point_status`,
    `is_overdue`, and `overdue_since` — the frontend does not derive
    any of these (§5.4.3 explicit prohibition).

    Program scope: honors the X-Program-Id contract (§6.7) via the
    caller's claim set passed through auth.programs.  Multi-program
    `*` scope is accepted by this endpoint per §5.2 (Inspection Queue
    is one of the enumerated list pages that support "All").
    """
    items, total = await insp_svc.list_inspection_queue(
        session,
        filters=InspectionQueueFilters(
            state=state,
            operation_id=operation_id,
            safety_critical=safety_critical,
        ),
        program_codes=auth.programs,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse[InspectionQueueRowOut](
        items=[InspectionQueueRowOut.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


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
