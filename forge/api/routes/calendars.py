#!/usr/bin/env python3
"""
Module:   Calendar Routes
Purpose:  HTTP endpoints for WorkCalendar CRUD (spec §5.10 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Thin routes per CLAUDE.md.  Permissions follow spec §5.0.2: write
  operations require sched:create, reads require sched:read.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.api.program_scope import require_feature
from forge.schemas.calendar import (
    CalendarCreate,
    CalendarOut,
    CalendarUpdate,
)
from forge.services import calendar_service as cal_svc

# Calendars are part of the schedule surface (UX spec §5.7) and share
# the schedule_enabled feature flag.  Router-level dependency keeps
# the gate consistent across every calendar handler.
router = APIRouter(
    prefix="/api/calendars",
    tags=["calendars"],
    dependencies=[Depends(require_feature("schedule_enabled"))],
)


@router.post("", response_model=CalendarOut, status_code=201)
async def create_calendar(
    data: CalendarCreate,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> CalendarOut:
    """Create a work calendar."""
    cal = await cal_svc.create_calendar(session, data, uuid.UUID(auth.sub))
    await session.commit()
    await session.refresh(cal)
    return CalendarOut.model_validate(cal)


@router.get("", response_model=dict[str, object])
async def list_calendars(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("sched:read")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List calendars (paginated)."""
    del auth
    items, total = await cal_svc.list_calendars(
        session, page=page, page_size=page_size,
    )
    return {
        "items": [CalendarOut.model_validate(c) for c in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/{calendar_id}", response_model=CalendarOut)
async def get_calendar(
    calendar_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("sched:read")),
    session: AsyncSession = Depends(get_db),
) -> CalendarOut:
    """Get a calendar by id."""
    del auth
    cal = await cal_svc.get_calendar(session, calendar_id)
    return CalendarOut.model_validate(cal)


@router.patch("/{calendar_id}", response_model=CalendarOut)
async def update_calendar(
    calendar_id: uuid.UUID,
    data: CalendarUpdate,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> CalendarOut:
    """Update a calendar (version-checked)."""
    cal = await cal_svc.update_calendar(
        session, calendar_id, data, uuid.UUID(auth.sub),
    )
    await session.commit()
    await session.refresh(cal)
    return CalendarOut.model_validate(cal)
