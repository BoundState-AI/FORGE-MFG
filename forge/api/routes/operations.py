#!/usr/bin/env python3
"""
Module:   Operation Routes
Purpose:  HTTP endpoints for operation authoring, execution, labor, and the
          enriched detail read surface (UX spec v1.5.1 §5.4.8).

BoundState AI 2026
Version: 0.2.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes delegate to operation_service for all business logic.
  Operation start requires role and shift for the labor entry.
  Labor update checks labor:update_own vs labor:update_any.

  GET /api/operations/{op_id} returns the enriched
  OperationDetailOut shape named by the engineer Operation Detail
  page (§5.4.11) and the SSE reconnect contract (§6.5.2 — every SSE
  reconnect refetches this endpoint as the authoritative
  reconciliation surface).  Authorization rides on the §6.7.4
  entity-detail GET exception in the program-scope middleware.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, get_auth_context, require_permission
from forge.api.concurrency import resolve_version_assertion, strong_etag
from forge.api.deps import get_db
from forge.schemas.labor import LaborCreate, LaborOut, LaborUpdate
from forge.schemas.operation import (
    OperationCreate,
    OperationDetailOut,
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


@router.get("/api/operations/{op_id}", response_model=OperationDetailOut)
async def get_operation(
    op_id: uuid.UUID,
    response: Response,
    auth: AuthContext = Depends(require_permission("op:read")),
    session: AsyncSession = Depends(get_db),
) -> OperationDetailOut:
    """Enriched operation detail per UX spec v1.5.1 §5.4.8.

    Emits `ETag: "<version>"` per §6.3.1 so the client can send it
    back as `If-Match` on subsequent operation mutations.

    Drives the engineer Operation Detail page (§5.4.11) and the SSE
    reconnect contract (§6.5.2 — clients refetch this endpoint on every
    reconnect as the authoritative state-reconciliation surface,
    regardless of any best-effort SSE replay).

    Authorization (§6.7.4 entity-detail GET exception): the X-Program-Id
    middleware skips header membership comparison on this route; service-
    layer enforcement against entity.program_id (via parent WO) returns
    404 on cross-program per the §3.2 existence-leak rule.
    """
    detail = await op_svc.get_operation_detail(
        session, op_id, program_codes=auth.programs,
    )
    # detail is dict[str, object]; version is always int by construction.
    version = detail["version"]
    assert isinstance(version, int)
    response.headers["ETag"] = strong_etag(version)
    return OperationDetailOut.model_validate(detail)


@router.patch("/api/operations/{op_id}", response_model=OperationOut)
async def update_operation(
    op_id: uuid.UUID,
    data: OperationUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("op:update")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Update operation (draft WO only, version check).

    §6.3.1 concurrency: body.version OR If-Match — route resolves
    via `resolve_version_assertion`.
    """
    expected = resolve_version_assertion(data.version, if_match)
    data = data.model_copy(update={"version": expected})
    op = await op_svc.update_operation(
        session, op_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    response.headers["ETag"] = strong_etag(op.version)
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
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("op:execute")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Start operation, create labor entry.  §6.3.1 version assertion."""
    expected = resolve_version_assertion(body.version, if_match)
    op = await op_svc.start_operation(
        session, op_id, expected, uuid.UUID(auth.sub),
        role=body.role, shift=body.shift,
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    response.headers["ETag"] = strong_etag(op.version)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/stop", response_model=OperationOut)
async def stop_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("op:execute")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Stop active labor entry for the current user."""
    expected = resolve_version_assertion(body.version, if_match)
    op = await op_svc.stop_operation(
        session, op_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    response.headers["ETag"] = strong_etag(op.version)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/complete", response_model=OperationOut)
async def complete_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("op:execute")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Complete operation (rules checked)."""
    expected = resolve_version_assertion(body.version, if_match)
    op = await op_svc.complete_operation(
        session, op_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    response.headers["ETag"] = strong_etag(op.version)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/hold", response_model=OperationOut)
async def hold_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("op:hold")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Put operation on hold."""
    expected = resolve_version_assertion(body.version, if_match)
    op = await op_svc.hold_operation(
        session, op_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    response.headers["ETag"] = strong_etag(op.version)
    return OperationOut.model_validate(op)


@router.post("/api/operations/{op_id}/rework", response_model=OperationOut)
async def rework_operation(
    op_id: uuid.UUID,
    body: OperationTransition,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("op:rework")),
    session: AsyncSession = Depends(get_db),
) -> OperationOut:
    """Send operation to rework."""
    expected = resolve_version_assertion(body.version, if_match)
    op = await op_svc.rework_operation(
        session, op_id, expected, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(op)
    response.headers["ETag"] = strong_etag(op.version)
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
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Update a labor entry (own or any per permission).

    Spec §5.3: labor:update_own allows updating own entries,
    labor:update_any allows updating any entry.  §6.3.1 version
    assertion via body.version OR If-Match.
    """
    can_any = auth.has_permission("labor:update_any")
    can_own = auth.has_permission("labor:update_own")
    if not can_any and not can_own:
        raise HTTPException(
            status_code=403,
            detail="Missing required permission: labor:update_own or labor:update_any",
        )

    expected = resolve_version_assertion(data.version, if_match)
    data = data.model_copy(update={"version": expected})
    labor = await op_svc.update_labor_entry(
        session, labor_id, data, uuid.UUID(auth.sub),
        can_update_any=can_any,
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(labor)
    response.headers["ETag"] = strong_etag(labor.version)
    return LaborOut.model_validate(labor)
