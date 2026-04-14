#!/usr/bin/env python3
"""
Module:   Calendar Service
Purpose:  CRUD for WorkCalendar entities (spec §5.10 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Calendars are shared master data (no program_id on WorkCalendar per
  spec §3.1), so this service does not apply program-scope filtering.
  Optimistic version checks are enforced on updates (spec §5.15).
  The service is deliberately thin — calendars are reference data; no
  state transitions, SoD, or transaction gates beyond optimistic
  locking are warranted at this level.
"""

from __future__ import annotations

import uuid as _uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType
from forge.models.schedule import WorkCalendar
from forge.schemas.calendar import CalendarCreate, CalendarUpdate
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)


async def create_calendar(
    session: AsyncSession,
    data: CalendarCreate,
    user_id: _uuid.UUID,
) -> WorkCalendar:
    """Create a WorkCalendar.

    State leaving: (none).  State entering: stored.
    Invariant: working_days values are 0..6 and working_hours_per_day
    is positive — both enforced at the schema boundary.
    """
    cal = WorkCalendar(
        name=data.name,
        working_hours_per_day=data.working_hours_per_day,
        working_days=sorted(set(data.working_days)),
        holidays=sorted(set(data.holidays)),
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(cal)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="calendar.create",
        entity_type="work_calendar",
        entity_id=cal.id,
        after_state={"name": cal.name},
    )
    return cal


async def get_calendar(
    session: AsyncSession, calendar_id: _uuid.UUID,
) -> WorkCalendar:
    """Load a calendar by id, 404 if missing."""
    cal = await session.get(WorkCalendar, calendar_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="Calendar not found")
    return cal


async def list_calendars(
    session: AsyncSession, *, page: int = 1, page_size: int = 50,
) -> tuple[list[WorkCalendar], int]:
    """List calendars paginated.  Shared master data — no program filter."""
    total_result = await session.execute(select(func.count(WorkCalendar.id)))
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(
        select(WorkCalendar)
        .order_by(WorkCalendar.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def update_calendar(
    session: AsyncSession,
    calendar_id: _uuid.UUID,
    data: CalendarUpdate,
    user_id: _uuid.UUID,
) -> WorkCalendar:
    """Partial update with optimistic version check (spec §5.15).

    State leaving: stored.  State entering: stored.
    Invariant: data.version must match the current calendar version.
    """
    cal = await get_calendar(session, calendar_id)

    try:
        check_version(cal, data.version, entity_type="WorkCalendar")
    except VersionConflictError as e:
        raise version_conflict_response(e) from e

    if data.name is not None:
        cal.name = data.name
    if data.working_hours_per_day is not None:
        cal.working_hours_per_day = data.working_hours_per_day
    if data.working_days is not None:
        cal.working_days = sorted(set(data.working_days))
    if data.holidays is not None:
        cal.holidays = sorted(set(data.holidays))
    if data.effective_from is not None:
        cal.effective_from = data.effective_from
    if data.effective_to is not None:
        cal.effective_to = data.effective_to

    cal.updated_by = user_id
    increment_version(cal)
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=user_id,
        user_role=None,
        action_type="calendar.update",
        entity_type="work_calendar",
        entity_id=cal.id,
        after_state={"name": cal.name},
    )
    return cal
