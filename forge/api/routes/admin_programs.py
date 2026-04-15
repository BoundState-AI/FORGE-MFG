#!/usr/bin/env python3
"""
Module:   Admin Programs Routes
Purpose:  HTTP endpoints for admin program CRUD (UX spec v1.5.1 §5.10
          "Programs: List with member count, active toggle, feature flags").

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Split into a dedicated router rather than added to admin.py because
  admin.py already prefixes `/api/admin/users` — re-using that router
  would force every program route to live under /users.

  The /api/admin/* prefix is exempt from the X-Program-Id wire
  contract per UX spec §6.7 (R5-R2-ISSUE-008): admin endpoints manage
  cross-program identity and configuration.  Authorization is the
  per-route admin:manage permission, enforced by admin_service via
  the live-DB zero-drift check (build spec §5.0.4).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.concurrency import resolve_version_assertion, strong_etag
from forge.api.deps import get_db
from forge.schemas.admin import (
    ProgramCreate,
    ProgramListItemOut,
    ProgramOut,
    ProgramUpdate,
)
from forge.schemas.common import PaginatedResponse
from forge.services import admin_service

router = APIRouter(prefix="/api/admin/programs", tags=["admin"])


@router.get("", response_model=PaginatedResponse[ProgramListItemOut])
async def list_programs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ProgramListItemOut]:
    """List programs with member count + feature flags (paginated).

    Response envelope is the typed PaginatedResponse[T] (UX spec
    §5.15) so the OpenAPI schema carries the precise item shape
    and generated clients can bind to typed request/response models.
    """
    rows, total = await admin_service.list_programs(
        session, page=page, page_size=page_size,
        actor_id=uuid.UUID(auth.sub),
    )
    items = [
        ProgramListItemOut(
            id=program.id,
            code=program.code,
            name=program.name,
            active=program.active,
            schedule_enabled=program.schedule_enabled,
            oracle_access=program.oracle_access,
            overdue_threshold_hours=program.overdue_threshold_hours,
            version=program.version,
            member_count=member_count,
        )
        for program, member_count in rows
    ]
    return PaginatedResponse[ProgramListItemOut](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size if total > 0 else 0,
    )


@router.get("/{program_id}", response_model=ProgramOut)
async def get_program(
    program_id: uuid.UUID,
    response: Response,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> ProgramOut:
    """Single program detail.  Emits strong ETag per §6.3.1."""
    program = await admin_service.get_program(
        session, program_id, actor_id=uuid.UUID(auth.sub),
    )
    response.headers["ETag"] = strong_etag(program.version)
    return ProgramOut.model_validate(program)


@router.post("", response_model=ProgramOut, status_code=201)
async def create_program(
    data: ProgramCreate,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> ProgramOut:
    """Create a new program.  Idempotency-Key enforced by middleware."""
    program = await admin_service.create_program(
        session, data, actor_id=uuid.UUID(auth.sub),
    )
    await session.commit()
    await session.refresh(program)
    return ProgramOut.model_validate(program)


@router.patch("/{program_id}", response_model=ProgramOut)
async def update_program(
    program_id: uuid.UUID,
    data: ProgramUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> ProgramOut:
    """Update program fields with §6.3.1 concurrency (body or If-Match)."""
    expected = resolve_version_assertion(data.version, if_match)
    data = data.model_copy(update={"version": expected})
    program = await admin_service.update_program(
        session, program_id, data, actor_id=uuid.UUID(auth.sub),
    )
    await session.commit()
    await session.refresh(program)
    response.headers["ETag"] = strong_etag(program.version)
    return ProgramOut.model_validate(program)
