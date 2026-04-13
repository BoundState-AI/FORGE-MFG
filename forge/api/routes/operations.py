#!/usr/bin/env python3
"""
Module:   Operation Routes
Purpose:  HTTP endpoints for operation authoring, execution, and labor (spec §5.2-§5.3).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Routes delegate to operation_service for all business logic.
  Operation start requires role and shift for the labor entry.
  Labor update checks labor:update_own vs labor:update_any.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, get_auth_context, require_permission
from forge.api.deps import get_db
from forge.schemas.labor import LaborCreate, LaborOut, LaborUpdate
from forge.schemas.operation import (
    OperationCreate,
    OperationOut,
    OperationReorder,
    OperationTransition,
    OperationUpdate,
)
from forge.services import operation_service as op_svc

router = APIRouter(tags=["operations"])


# --- Start request includes role/shift for the labor entry ---
class OperationStartRequest(OperationTransition):
    """Start request includes role and shift for the labor entry."""

    role: str
    shift: str


# ---------------------------------------------------------------------------
# Operation authoring (draft WO only)
# ---------------------------------------------------------------------------


@router.post(
    "/api/work-orders/{wo_id}/operations",
    response_model=OperationOut,
    status_code=201,
)
async def create_operation(
    wo_id: uuid.UUID,
    data: OperationCreate,
    auth: AuthContext = Depends(require_permission("op:create")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Create operation on a draft work order."""
    op = await op_svc.create_operation(
        session, wo_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    return OperationOut.model_validate(op)


@router.patch("/api/operations/{op_id}", response_model=OperationOut)
async def update_operation(
    op_id: uuid.UUID,
    data: OperationUpdate,
    auth: AuthContext = Depends(require_permission("op:update")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Update operation (draft WO only, version check)."""
    op = await op_svc.update_operation(
        session, op_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    return OperationOut.model_validate(op)


@router.delete(
    "/api/operations/{op_id}", status_code=204, response_model=None
)
async def delete_operation(
    op_id: uuid.UUID,
    version: int = Query(..., description="Optimistic lock version"),
    auth: AuthContext = Depends(require_permission("op:delete")),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete operation (draft WO only, version check)."""
    await op_svc.delete_operation(
        session, op_id, version, program_codes=auth.programs
    )
    await session.commit()


@router.post(
    "/api/work-orders/{wo_id}/operations/reorder",
    response_model=list[OperationOut],
)
async def reorder_operations(
    wo_id: uuid.UUID,
    data: OperationReorder,
    auth: AuthContext = Depends(require_permission("op:update")),
    session: AsyncSession = Depends(get_db),
) -> list[OperationOut]:
    """Reorder operations by sequence (draft WO only)."""
    ops = await op_svc.reorder_operations(
        session, wo_id, data.operation_ids, uuid.UUID(auth.sub),
        version=data.version, program_codes=auth.programs,
    )
    await session.commit()
    for op in ops:
        await session.refresh(op)
    return [OperationOut.model_validate(op) for op in ops]


# ---------------------------------------------------------------------------
# Operation execution
# ---------------------------------------------------------------------------


@router.post("/api/operations/{op_id}/start", response_model=OperationOut)
async def start_operation(
    op_id: uuid.UUID,
    body: OperationStartRequest,
    auth: AuthContext = Depends(require_permission("op:execute")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Start operation, create labor entry."""
    op = await op_svc.start_operation(
        session, op_id, body.version, uuid.UUID(auth.sub),
        role=body.role, shift=body.shift,
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/stop", response_model=OperationOut)
async def stop_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    auth: AuthContext = Depends(require_permission("op:execute")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Stop active labor entry for the current user."""
    op = await op_svc.stop_operation(
        session, op_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/complete", response_model=OperationOut)
async def complete_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    auth: AuthContext = Depends(require_permission("op:execute")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Complete operation (rules checked)."""
    op = await op_svc.complete_operation(
        session, op_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/hold", response_model=OperationOut)
async def hold_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    auth: AuthContext = Depends(require_permission("op:hold")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Put operation on hold."""
    op = await op_svc.hold_operation(
        session, op_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/rework", response_model=OperationOut)
async def rework_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    auth: AuthContext = Depends(require_permission("op:rework")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Send operation to rework."""
    op = await op_svc.rework_operation(
        session, op_id, body.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    return OperationOut.model_validate(op)


# ---------------------------------------------------------------------------
# Labor
# ---------------------------------------------------------------------------


@router.post(
    "/api/operations/{op_id}/labor",
    response_model=LaborOut,
    status_code=201,
)
async def create_labor(
    op_id: uuid.UUID,
    data: LaborCreate,
    auth: AuthContext = Depends(require_permission("labor:create")),
    session: AsyncSession = Depends(get_db),
) -> LaborOut:
    """Create a labor entry on an operation."""
    labor = await op_svc.create_labor_entry(
        session, op_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(labor)
    return LaborOut.model_validate(labor)


@router.patch("/api/labor/{labor_id}", response_model=LaborOut)
async def update_labor(
    labor_id: uuid.UUID,
    data: LaborUpdate,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Update a labor entry (own or any per permission).

    Spec §5.3: labor:update_own allows updating own entries,
    labor:update_any allows updating any entry.
    """
    can_any = auth.has_permission("labor:update_any")
    can_own = auth.has_permission("labor:update_own")
    if not can_any and not can_own:
        raise HTTPException(
            status_code=403,
            detail="Missing required permission: labor:update_own or labor:update_any",
        )

    labor = await op_svc.update_labor_entry(
        session, labor_id, data, uuid.UUID(auth.sub),
        can_update_any=can_any,
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(labor)
    return LaborOut.model_validate(labor)
