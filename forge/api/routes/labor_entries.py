#!/usr/bin/env python3
"""
Module:   Labor Entries Routes
Purpose:  Canonical `/api/labor-entries/*` surface: PATCH for
          corrections (slice 5B-9 normalization) plus the three
          labor-control endpoints from UX spec v1.5.1 §5.4.9 +
          §5.4.5 (force-stop, stop-request, flag-correction).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Spec §3.2 names `/api/labor-entries/:id` as the canonical path
  for correction + labor-control surfaces.  The legacy alias
  `/api/labor/{id}` PATCH in forge.api.routes.operations is kept
  as a thin compatibility shim for tests that pre-date this
  normalization; the body semantics are identical, so both routes
  delegate into the same service.  A later slice can delete the
  alias once all callers migrate.

  Routes are thin — business logic lives in:
    - forge.services.operation_service.update_labor_entry for PATCH
    - forge.services.stop_request_service.* for the three new
      endpoints

  Permission boundaries (UX spec §5.4.9 roles):
    - PATCH:                labor:update_own | labor:update_any
      (inherited from the pre-5B-12 route; unchanged).
    - /force-stop:          labor:force_stop (engineer only —
      qa_manager's labor:update_any does NOT imply this authority
      per §5.4.9 LABOR-STOP-003).
    - /stop-request:        labor:request_stop (operator only).
    - /flag-correction:     labor:flag_correction (operator only).

  Each new permission was added in migration f4d8b2e6c31a with DB
  seed alignment.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, get_auth_context, require_permission
from forge.api.deps import get_db
from forge.schemas.labor import LaborOut, LaborUpdate
from forge.schemas.stop_request import (
    FlagCorrectionIn,
    ForceStopIn,
    StopRequestIn,
    StopRequestOut,
)
from forge.services import operation_service as op_svc
from forge.services import stop_request_service as stop_svc

router = APIRouter(prefix="/api/labor-entries", tags=["labor-entries"])


@router.patch("/{labor_id}", response_model=LaborOut)
async def update_labor_entry(
    labor_id: uuid.UUID,
    data: LaborUpdate,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Canonical PATCH for labor-entry corrections (UX spec §3.2).

    Mirrors the legacy /api/labor/{id} handler in operations.py —
    both delegate to op_svc.update_labor_entry.  Permission split
    (labor:update_own vs labor:update_any) is unchanged from the
    pre-normalization surface; only the URL moved.
    """
    can_any = auth.has_permission("labor:update_any")
    can_own = auth.has_permission("labor:update_own")
    if not can_any and not can_own:
        raise HTTPException(
            status_code=403,
            detail=(
                "Missing required permission: "
                "labor:update_own or labor:update_any"
            ),
        )

    labor = await op_svc.update_labor_entry(
        session, labor_id, data, uuid.UUID(auth.sub),
        can_update_any=can_any,
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(labor)
    return LaborOut.model_validate(labor)


@router.post("/{labor_id}/force-stop", response_model=LaborOut)
async def force_stop(
    labor_id: uuid.UUID,
    data: ForceStopIn,
    auth: AuthContext = Depends(require_permission("labor:force_stop")),
    session: AsyncSession = Depends(get_db),
) -> LaborOut:
    """Engineer stops another user's active labor entry (UX §5.4.9).

    Emits labor.stopped_by_engineer, transitions any pending
    stop-request on this entry to `fulfilled`, and returns the
    updated LaborEntry.  Service-layer enforcement catches own-
    entry attempts (LABOR-STOP-002), already-stopped entries
    (LABOR-STOP-001), and version conflicts (409).
    """
    labor = await stop_svc.apply_force_stop(
        session, labor_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(labor)
    return LaborOut.model_validate(labor)


@router.post(
    "/{labor_id}/stop-request",
    response_model=StopRequestOut,
    status_code=201,
)
async def create_stop_request(
    labor_id: uuid.UUID,
    data: StopRequestIn,
    auth: AuthContext = Depends(require_permission("labor:request_stop")),
    session: AsyncSession = Depends(get_db),
) -> StopRequestOut:
    """Operator requests an engineer stop another user's entry
    (UX §5.4.9).

    Creates a pending StopRequest, emits labor.stop_requested.
    Duplicate pending requests are blocked at the DB by the partial
    unique index and surface as LABOR-STOP-REQ-002.
    """
    result = await stop_svc.apply_stop_request(
        session, labor_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    return result


@router.post(
    "/{labor_id}/flag-correction",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def flag_correction(
    labor_id: uuid.UUID,
    data: FlagCorrectionIn,
    auth: AuthContext = Depends(require_permission("labor:flag_correction")),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Operator flags a labor entry for engineer correction review
    (UX §5.4.5).

    No state transition — audit-only.  Returns 204 because the
    resource state did not change from the caller's perspective;
    the attention signal lives in the audit trail.  `response_class=
    Response` + explicit empty 204 body is FastAPI's no-content
    contract — a response_model + None body would fail the
    is_body_allowed_for_status_code assert at import time.
    """
    await stop_svc.apply_flag_correction(
        session, labor_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
