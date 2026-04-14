#!/usr/bin/env python3
"""
Module:   Schedule Routes
Purpose:  HTTP endpoints for BuildSchedule lifecycle, ScheduleLink CRUD
          with cycle detection, Milestone CRUD, critical-path and
          what-if analysis (spec §5.9 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Thin routes per CLAUDE.md.  Cycle detection, schedule-state gating,
  version checks, and program-scope enforcement all live in the
  service layer — the route layer only wires permissions and
  serialization.  Permissions follow spec §5.0.2:
    - sched:create  — schedule/link/milestone writes
    - sched:baseline — baseline only
    - sched:read    — critical-path + link list
    - sched:what_if — what-if analysis
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_db
from forge.schemas.schedule import (
    CpmNodeOut,
    CriticalPathOut,
    MilestoneCreate,
    MilestoneOut,
    MilestoneUpdate,
    ScheduleCreate,
    ScheduleLinkCreate,
    ScheduleLinkOut,
    ScheduleLinkUpdate,
    ScheduleOut,
    ScheduleTransition,
    WhatIfOut,
    WhatIfRequest,
)
from forge.services import schedule_service as sched_svc

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


# ---------------------------------------------------------------------------
# Schedule create + baseline
# ---------------------------------------------------------------------------


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    data: ScheduleCreate,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> ScheduleOut:
    """Create a BuildSchedule in draft."""
    sched = await sched_svc.create_schedule(
        session, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(sched)
    return ScheduleOut.model_validate(sched)


@router.post("/{schedule_id}/baseline", response_model=ScheduleOut)
async def baseline_schedule(
    schedule_id: uuid.UUID,
    data: ScheduleTransition,
    auth: AuthContext = Depends(require_permission("sched:baseline")),
    session: AsyncSession = Depends(get_db),
) -> ScheduleOut:
    """Baseline a schedule (draft → baselined)."""
    sched = await sched_svc.baseline_schedule(
        session, schedule_id, data.version, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(sched)
    return ScheduleOut.model_validate(sched)


@router.get("/{schedule_id}/critical-path", response_model=CriticalPathOut)
async def get_critical_path(
    schedule_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("sched:read")),
    session: AsyncSession = Depends(get_db),
) -> CriticalPathOut:
    """Compute and return the schedule's critical path."""
    sched, result, forecast = await sched_svc.get_critical_path(
        session, schedule_id, program_codes=auth.programs,
    )
    nodes = [
        CpmNodeOut(
            operation_id=n.operation_id,
            early_start=n.early_start, early_finish=n.early_finish,
            late_start=n.late_start, late_finish=n.late_finish,
            slack=n.slack,
        )
        for n in result.nodes.values()
    ]
    return CriticalPathOut(
        schedule_id=sched.id,
        forecast_finish=forecast,
        total_duration_hours=result.total_duration_hours,
        critical_path=result.critical_path,
        nodes=nodes,
    )


@router.post("/{schedule_id}/what-if", response_model=WhatIfOut)
async def run_what_if(
    schedule_id: uuid.UUID,
    data: WhatIfRequest,
    auth: AuthContext = Depends(require_permission("sched:what_if")),
    session: AsyncSession = Depends(get_db),
) -> WhatIfOut:
    """Run a what-if impact analysis (read-only; never mutates state)."""
    result = await sched_svc.run_what_if(
        session, schedule_id, data, program_codes=auth.programs,
    )
    return WhatIfOut.model_validate(result)


# ---------------------------------------------------------------------------
# Schedule Links
# ---------------------------------------------------------------------------


@router.post(
    "/{schedule_id}/links", response_model=ScheduleLinkOut, status_code=201,
)
async def create_link(
    schedule_id: uuid.UUID,
    data: ScheduleLinkCreate,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> ScheduleLinkOut:
    """Create a schedule link (cycle-checked)."""
    link = await sched_svc.create_link(
        session, schedule_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(link)
    return ScheduleLinkOut.model_validate(link)


@router.patch(
    "/{schedule_id}/links/{link_id}", response_model=ScheduleLinkOut,
)
async def update_link(
    schedule_id: uuid.UUID,
    link_id: uuid.UUID,
    data: ScheduleLinkUpdate,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> ScheduleLinkOut:
    """Update a schedule link (cycle-checked, version-checked)."""
    link = await sched_svc.update_link(
        session, schedule_id, link_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(link)
    return ScheduleLinkOut.model_validate(link)


@router.delete(
    "/{schedule_id}/links/{link_id}", status_code=204, response_model=None,
)
async def delete_link(
    schedule_id: uuid.UUID,
    link_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a schedule link (draft-only)."""
    await sched_svc.delete_link(
        session, schedule_id, link_id, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()


@router.get(
    "/{schedule_id}/links", response_model=list[ScheduleLinkOut],
)
async def list_links(
    schedule_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("sched:read")),
    session: AsyncSession = Depends(get_db),
) -> list[ScheduleLinkOut]:
    """List all links for a schedule."""
    links = await sched_svc.list_links(
        session, schedule_id, program_codes=auth.programs,
    )
    return [ScheduleLinkOut.model_validate(link) for link in links]


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------


@router.post(
    "/{schedule_id}/milestones",
    response_model=MilestoneOut,
    status_code=201,
)
async def create_milestone(
    schedule_id: uuid.UUID,
    data: MilestoneCreate,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> MilestoneOut:
    """Create a milestone."""
    milestone = await sched_svc.create_milestone(
        session, schedule_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(milestone)
    return MilestoneOut.model_validate(milestone)


@router.patch(
    "/{schedule_id}/milestones/{milestone_id}",
    response_model=MilestoneOut,
)
async def update_milestone(
    schedule_id: uuid.UUID,
    milestone_id: uuid.UUID,
    data: MilestoneUpdate,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> MilestoneOut:
    """Update a milestone (version-checked)."""
    milestone = await sched_svc.update_milestone(
        session, schedule_id, milestone_id, data, uuid.UUID(auth.sub),
        program_codes=auth.programs,
    )
    await session.commit()
    await session.refresh(milestone)
    return MilestoneOut.model_validate(milestone)


@router.delete(
    "/{schedule_id}/milestones/{milestone_id}",
    status_code=204, response_model=None,
)
async def delete_milestone(
    schedule_id: uuid.UUID,
    milestone_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("sched:create")),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a milestone (draft-only)."""
    await sched_svc.delete_milestone(
        session, schedule_id, milestone_id,
        program_codes=auth.programs,
    )
    await session.commit()
