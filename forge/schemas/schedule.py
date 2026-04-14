#!/usr/bin/env python3
"""
Module:   Schedule Schemas
Purpose:  Pydantic request/response models for schedule, link, milestone,
          critical-path, and what-if endpoints (spec §5.9 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Each lifecycle step has its own model so the required fields for
  that step are enforced at the schema boundary.  WhatIfRequest uses
  override objects (operation_hours, link_lag, added_links) keyed by
  id so callers can change exactly what they want to probe without
  re-submitting the entire schedule.  WhatIfOut returns a structured
  delta so UIs can render "baselined finish → projected finish, delta
  X working hours, critical path changed yes/no" without reshaping.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import LinkType, MilestoneStatus, ScheduleStatus

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


class ScheduleCreate(BaseModel):
    """Request body for POST /api/schedules."""

    work_order_id: uuid.UUID
    calendar_id: uuid.UUID
    baseline_start: datetime
    baseline_finish: datetime


class ScheduleTransition(BaseModel):
    """Request body for state-transition POST endpoints (baseline)."""

    version: int


class ScheduleOut(BaseModel):
    """BuildSchedule in API responses."""

    id: uuid.UUID
    work_order_id: uuid.UUID
    calendar_id: uuid.UUID
    baseline_start: datetime
    baseline_finish: datetime
    forecast_finish: datetime
    status: ScheduleStatus
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Schedule Link
# ---------------------------------------------------------------------------


class ScheduleLinkCreate(BaseModel):
    """Request body for POST /api/schedules/{id}/links."""

    predecessor_op_id: uuid.UUID
    successor_op_id: uuid.UUID
    link_type: LinkType = LinkType.finish_to_start
    lag_hours: float = 0.0


class ScheduleLinkUpdate(BaseModel):
    """Request body for PATCH /api/schedules/{id}/links/{link_id}."""

    version: int
    link_type: LinkType | None = None
    lag_hours: float | None = None


class ScheduleLinkOut(BaseModel):
    """ScheduleLink in API responses."""

    id: uuid.UUID
    schedule_id: uuid.UUID
    predecessor_op_id: uuid.UUID
    successor_op_id: uuid.UUID
    link_type: LinkType
    lag_hours: float
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Milestone
# ---------------------------------------------------------------------------


class MilestoneCreate(BaseModel):
    """Request body for POST /api/schedules/{id}/milestones."""

    name: str = Field(max_length=200)
    target_date: datetime
    program_milestone_id: str | None = Field(default=None, max_length=100)


class MilestoneUpdate(BaseModel):
    """Request body for PATCH /api/schedules/{id}/milestones/{mid}."""

    version: int
    name: str | None = Field(default=None, max_length=200)
    target_date: datetime | None = None
    program_milestone_id: str | None = Field(default=None, max_length=100)


class MilestoneOut(BaseModel):
    """Milestone in API responses."""

    id: uuid.UUID
    schedule_id: uuid.UUID
    name: str
    target_date: datetime
    status: MilestoneStatus
    program_milestone_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# CPM outputs
# ---------------------------------------------------------------------------


class CpmNodeOut(BaseModel):
    """Per-operation CPM output in working hours (relative to baseline_start).

    slack = late_start - early_start.  Zero-slack operations lie on
    the critical path.
    """

    operation_id: uuid.UUID
    early_start: float
    early_finish: float
    late_start: float
    late_finish: float
    slack: float


class CriticalPathOut(BaseModel):
    """Response body for GET /api/schedules/{id}/critical-path."""

    schedule_id: uuid.UUID
    forecast_finish: datetime
    total_duration_hours: float
    critical_path: list[uuid.UUID]
    nodes: list[CpmNodeOut]


# ---------------------------------------------------------------------------
# What-if
# ---------------------------------------------------------------------------


class WhatIfOperationOverride(BaseModel):
    """Hypothetical estimated_hours for an existing operation."""

    operation_id: uuid.UUID
    estimated_hours: float = Field(ge=0)


class WhatIfLinkOverride(BaseModel):
    """Hypothetical lag_hours for an existing schedule link."""

    link_id: uuid.UUID
    lag_hours: float


class WhatIfAddedLink(BaseModel):
    """Hypothetical new schedule link (not persisted)."""

    predecessor_op_id: uuid.UUID
    successor_op_id: uuid.UUID
    link_type: LinkType = LinkType.finish_to_start
    lag_hours: float = 0.0


class WhatIfRequest(BaseModel):
    """Request body for POST /api/schedules/{id}/what-if.

    All fields are optional; the service layer applies only the
    overrides provided and leaves the rest of the schedule at its
    current values.  The request never mutates persisted state
    (spec §8.3).
    """

    operation_overrides: list[WhatIfOperationOverride] = Field(
        default_factory=list,
    )
    link_overrides: list[WhatIfLinkOverride] = Field(default_factory=list)
    added_links: list[WhatIfAddedLink] = Field(default_factory=list)


class WhatIfOut(BaseModel):
    """Response body for POST /api/schedules/{id}/what-if.

    delta_hours is positive when the what-if finishes later than the
    current forecast.  cycle_detected indicates the hypothetical
    graph contained a cycle — in that case, the new forecast fields
    are copies of the current forecast and critical_path_changed is
    False (no comparison was possible).
    """

    schedule_id: uuid.UUID
    baseline_forecast_finish: datetime
    what_if_forecast_finish: datetime
    delta_hours: float
    critical_path: list[uuid.UUID]
    critical_path_changed: bool
    cycle_detected: bool
