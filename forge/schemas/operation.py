#!/usr/bin/env python3
"""
Module:   Operation Schemas
Purpose:  Pydantic request/response models for operation endpoints (spec §5.2).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  OperationCreate omits execution_cycle (initialized to 1 in the service
  layer) and status (always starts as pending).  inspection_hold and
  safety_critical default to False — the engineer sets them explicitly
  when the operation requires quality gates.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import OpStatus, ScheduleConstraint


class OperationCreate(BaseModel):
    """Request body for POST /api/work-orders/{id}/operations (draft WO only)."""

    description: str = Field(max_length=1000)
    sequence: int
    tooling_required: list[str] = []
    inspection_hold: bool = False
    safety_critical: bool = False
    sign_off_roles_required: list[str] = []
    estimated_hours: float = 0.0
    constraint_type: ScheduleConstraint = ScheduleConstraint.none
    constraint_date: datetime | None = None


class OperationUpdate(BaseModel):
    """Request body for PATCH /api/operations/{id} (draft WO only)."""

    version: int
    description: str | None = None
    sequence: int | None = None
    tooling_required: list[str] | None = None
    inspection_hold: bool | None = None
    safety_critical: bool | None = None
    sign_off_roles_required: list[str] | None = None
    estimated_hours: float | None = None
    constraint_type: ScheduleConstraint | None = None
    constraint_date: datetime | None = None


class OperationTransition(BaseModel):
    """Request body for operation state-transition endpoints."""

    version: int


class OperationReorder(BaseModel):
    """Request body for POST /api/work-orders/{id}/operations/reorder.

    version is the WO's version for optimistic locking — the WO
    is the parent entity being logically mutated by reordering.
    """

    version: int
    operation_ids: list[uuid.UUID]


class OperationOut(BaseModel):
    """Operation in API responses."""

    id: uuid.UUID
    work_order_id: uuid.UUID
    sequence: int
    description: str
    status: OpStatus
    execution_cycle: int
    inspection_hold: bool
    safety_critical: bool
    estimated_hours: float
    started_at: datetime | None
    completed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
