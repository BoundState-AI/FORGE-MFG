#!/usr/bin/env python3
"""
Module:   Work Order Schemas
Purpose:  Pydantic request/response models for work order endpoints (spec §5.1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Separate Create/Update/Transition schemas enforce that version is
  required on mutations (spec §5.15 optimistic locking) but absent on
  creation (version starts at 1 server-side).  WorkOrderOut exposes
  only the fields needed for API consumers, not internal ORM state.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import BuildPhase, Priority, WOStatus


class WorkOrderCreate(BaseModel):
    """Request body for POST /api/work-orders (creates in draft)."""

    program_id: uuid.UUID
    engine_serial: str = Field(max_length=100)
    module: str = Field(max_length=100)
    build_phase: BuildPhase
    priority: Priority = Priority.routine
    bom_revision_ref: str | None = None


class WorkOrderUpdate(BaseModel):
    """Request body for PATCH /api/work-orders/{id} (draft only).

    Spec §5.15: version is required for optimistic locking.
    """

    version: int
    engine_serial: str | None = None
    module: str | None = None
    build_phase: BuildPhase | None = None
    priority: Priority | None = None
    bom_revision_ref: str | None = None


class WorkOrderTransition(BaseModel):
    """Request body for state-transition POST endpoints.

    Spec §5.15: version is required on all state-transition endpoints.
    """

    version: int


class WorkOrderHoldIn(BaseModel):
    """Request body for POST /api/work-orders/{id}/hold (UX spec v1.5.1
    §5.3 Hold Cascading).

    `force_stop_labor` defaults to False (administrative hold).  When
    True, the server atomically stops all active labor entries on child
    operations and emits one `labor.stopped_by_hold` audit event per
    stopped entry.  `reason` is currently nullable — the spec mentions
    a hold reason but the frozen contract (§5.3 Hold Cascading
    subsection) only binds it as future SSE payload data, so the field
    is accepted and stored in the audit before_state but not required.
    """

    version: int
    force_stop_labor: bool = False
    reason: str | None = Field(default=None, max_length=2000)


class StoppedLaborEntryOut(BaseModel):
    """Single entry in WorkOrderHoldOut.stopped_labor_entries
    (UX spec v1.5.1 §5.3 Hold Cascading + §6.5.2 SSE payload
    requirements).

    Intentionally minimal: the frontend stop-work banner (§5.3) needs
    exactly `entry_id` / `operation_id` / `user_id` to decide whether
    the current user's own timer was force-stopped.  Timestamps are
    available via a follow-up read if needed.
    """

    entry_id: uuid.UUID
    operation_id: uuid.UUID
    user_id: uuid.UUID


class WorkOrderHoldOut(BaseModel):
    """Response for POST /api/work-orders/{id}/hold.

    Returns the post-hold WorkOrder plus the cascade metadata needed
    for the §5.3 stop-work banner and the §6.5.2 SSE payload.  When
    `force_stop_labor` is False, `stopped_labor_entries` is always an
    empty list — wire-stable so the client does not have to branch.
    """

    work_order: WorkOrderOut
    force_stop_labor: bool
    stopped_labor_entries: list[StoppedLaborEntryOut]


class WorkOrderOut(BaseModel):
    """Work order in API responses.

    `has_graph_issue` (UX spec v1.5.1 §5.4.6) ships on every WO
    response — list, detail, and every state-transition response —
    so the frontend never has to compose a separate fetch to render
    the [Fix Graph] / [Report Graph Issue] buttons.  In slice 5B-10
    the field is read-only surface; the active setters land with the
    graph-write endpoints in the next slice.
    """

    id: uuid.UUID
    wo_number: str
    program_id: uuid.UUID
    engine_serial: str
    module: str
    build_phase: BuildPhase
    status: WOStatus
    priority: Priority
    authored_by_id: uuid.UUID
    bom_revision_ref: str | None
    released_at: datetime | None
    released_by: uuid.UUID | None
    has_graph_issue: bool
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
