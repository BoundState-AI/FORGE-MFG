#!/usr/bin/env python3
"""
Module:   Admin Health Routes
Purpose:  HTTP endpoint for the Admin → System Health panel
          (UX spec v1.5.1 §5.10, build spec v1.4.2 §7 endpoint
          table: "GET /api/admin/health | admin:manage | System
          health: ECS, RDS, S3, audit chain").

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Routes are thin per CLAUDE.md; all probe + aggregation logic
  lives in admin_health_service.  The route handles three concerns:

    1. Permission — admin:manage (build spec §9).  Live-DB
       enforcement happens inside the service's audit-integrity
       reuse path, matching the zero-drift posture already
       established on /api/audit/*.

    2. Namespace exemption — /api/admin/* is already in the
       middleware PUBLIC_PREFIXES (forge.api.middleware.
       program_scope), so this endpoint inherits the
       X-Program-Id bypass automatically.  System Health is a
       cross-program concern and has no program scope to carry.

    3. Typed response — AdminHealthOut is shaped for OpenAPI so
       the 5B-21 client generator can bind the component wire
       contract without hand-rolling a dict-of-dicts.

  Dedicated router module (not stuffed into admin.py or
  admin_programs.py) keeps the permission dependency + response
  shape inspectable in one file.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.schemas.admin_health import AdminHealthOut
from forge.services import admin_health_service

router = APIRouter(prefix="/api/admin/health", tags=["admin"])


@router.get("", response_model=AdminHealthOut)
async def get_admin_health(
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> AdminHealthOut:
    """System Health composite for the admin panel (UX §5.10).

    Returns per-component health plus a rolled-up overall status.
    Components that the backend cannot truthfully measure (ECS
    task status in Phase 1) report `unknown` with an explanatory
    message — the slice deliberately does not fabricate AWS
    state.

    No X-Program-Id required: the /api/admin/* namespace is
    program-agnostic per UX spec v1.5.1 §6.7 (R5-R2-ISSUE-008),
    and the middleware bypasses the header contract for the
    entire prefix.
    """
    return await admin_health_service.summarize(
        session, actor_id=uuid.UUID(auth.sub),
    )
