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


class WorkOrderOut(BaseModel):
    """Work order in API responses."""

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
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
