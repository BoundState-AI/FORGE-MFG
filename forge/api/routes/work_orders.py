#!/usr/bin/env python3
"""
Module:   Work Order Routes
Purpose:  HTTP endpoints for work order CRUD and state transitions (spec §5.1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Routes are thin — they validate input, check permissions, delegate to
  services, and return responses.  Business logic lives in the service
  layer (CLAUDE.md build philosophy).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.schemas.work_order import (
    WorkOrderCreate,
    WorkOrderOut,
    WorkOrderTransition,
    WorkOrderUpdate,
)
from forge.services import work_order_service as wo_svc

router = APIRouter(prefix="/api/work-orders", tags=["work-orders"])


@router.post("", response_model=WorkOrderOut, status_code=201)
async def create_work_order(
    data: WorkOrderCreate,
    auth: AuthContext = Depends(require_permission("wo:create")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Create a new work order in draft status."""
    wo = await wo_svc.create_work_order(
        session, data, uuid.UUID(auth.sub)
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.get("", response_model=dict[str, object])
async def list_work_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("wo:read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List work orders (paginated)."""
    items, total = await wo_svc.list_work_orders(
        session, program_codes=auth.programs, page=page, page_size=page_size
    )
    return {
        "items": [WorkOrderOut.model_validate(wo) for wo in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/{wo_id}", response_model=WorkOrderOut)
async def get_work_order(
    wo_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("wo:read")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Get work order detail with operations (program-scoped)."""
    wo = await wo_svc.get_work_order(session, wo_id, program_codes=auth.programs)
    return WorkOrderOut.model_validate(wo)


@router.patch("/{wo_id}", response_model=WorkOrderOut)
async def update_work_order(
    wo_id: uuid.UUID,
    data: WorkOrderUpdate,
    auth: AuthContext = Depends(require_permission("wo:update")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Update work order fields (draft only, version check)."""
    wo = await wo_svc.update_work_order(
        session, wo_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/release", response_model=WorkOrderOut)
async def release_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    auth: AuthContext = Depends(require_permission("wo:release")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Release work order (draft → released).

    SOD-004 (spec §4.2, §5.1): author cannot release same WO.
    Enforcement happens in the service layer.
    """
    wo = await wo_svc.release_work_order(
        session, wo_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/recall", response_model=WorkOrderOut)
async def recall_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    auth: AuthContext = Depends(require_permission("wo:recall")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Recall work order (released → draft, UX spec v1.5.1 §5.3).

    Pre-activation graph-correction path: returns a released WO to
    draft so the engineer can fix the operation graph and re-release.
    Only valid while still in `released` — once an op starts, the
    WO atomically moves to `in_progress` and Recall is no longer
    appropriate (the correction path from there is Hold + Fix Graph).

    Engineer role only per §5.3 permission matrix, enforced by the
    `wo:recall` permission dependency.
    """
    wo = await wo_svc.recall_work_order(
        session, wo_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/hold", response_model=WorkOrderOut)
async def hold_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    auth: AuthContext = Depends(require_permission("wo:hold")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Put work order on hold."""
    wo = await wo_svc.hold_work_order(
        session, wo_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/resume", response_model=WorkOrderOut)
async def resume_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    auth: AuthContext = Depends(require_permission("wo:resume")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Resume work order from hold."""
    wo = await wo_svc.resume_work_order(
        session, wo_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/complete", response_model=WorkOrderOut)
async def complete_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    auth: AuthContext = Depends(require_permission("wo:complete")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Mark work order complete (all ops must be complete)."""
    wo = await wo_svc.complete_work_order(
        session, wo_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)


@router.post("/{wo_id}/close", response_model=WorkOrderOut)
async def close_work_order(
    wo_id: uuid.UUID,
    body: WorkOrderTransition,
    auth: AuthContext = Depends(require_permission("wo:close")),
    session: AsyncSession = Depends(get_db),
) -> WorkOrderOut:
    """Close work order (final QA closure)."""
    wo = await wo_svc.close_work_order(
        session, wo_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(wo)
    return WorkOrderOut.model_validate(wo)
