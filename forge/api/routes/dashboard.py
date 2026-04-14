#!/usr/bin/env python3
"""
Module:   Dashboard Routes
Purpose:  HTTP endpoint for the at-a-glance dashboard summary
          (UX spec v1.5.1 §5.1, build spec v1.4.2 §7 endpoint table).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md build philosophy — aggregation logic
  lives in dashboard_service.summarize.  The route handles just four
  concerns:

  1. Permission: `dash:read` (build spec §9 permission table).
  2. Program scope: single-program only, resolved via
     forge.api.program_scope.require_program_id.  Dashboard is
     intentionally NOT on the middleware's multi-program allowlist,
     so `X-Program-Id: *` is rejected upstream and this dependency
     defends against any future bypass.
  3. Role context: the service filters tiles by caller roles per
     UX §5.1 audience language; the route passes auth.roles through.
  4. Response shape: DashboardSummaryOut is typed so the OpenAPI
     client generator (slice 5B-21) can bind to the exact wire
     contract without hand-rolling the tile structure.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.api.program_scope import require_program_id
from forge.schemas.dashboard import DashboardSummaryOut
from forge.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryOut)
async def get_summary(
    auth: AuthContext = Depends(require_permission("dash:read")),
    program_id: str = Depends(require_program_id),
    session: AsyncSession = Depends(get_db),
) -> DashboardSummaryOut:
    """Dashboard summary for a single program (UX spec §5.1).

    Program scope: `X-Program-Id` must resolve to a single UUID.
    The middleware rejects `*` on this route (dashboard is not on
    the multi-program allowlist), and require_program_id defends
    in depth.  The service resolves the UUID to the program's
    `code` and scopes every aggregate to that one program.

    Tile audience: UX §5.1 names Unresolved Conditional Findings
    and Overdue Inspections as "QA manager dashboard additions."
    Tiles are filtered by caller.roles in the service layer, not
    hidden client-side — a UI bug cannot leak management-only
    counts to operators or inspectors.

    Returns 400 MISSING_PROGRAM_CONTEXT if the header is absent,
    400 MULTI_PROGRAM_NOT_SUPPORTED on `*`, 403 if the resolved
    program is not in the caller's memberships, or 200 with the
    summary otherwise.
    """
    program_uuid = uuid.UUID(program_id)
    return await dashboard_service.summarize(
        session,
        program_uuid=program_uuid,
        caller_roles=auth.roles,
    )
