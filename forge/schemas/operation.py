#!/usr/bin/env python3
"""
Module:   Operation Schemas
Purpose:  Pydantic request/response models for operation endpoints, including
          the enriched detail surface required by UX spec v1.5.1 §5.4.8.

BoundState AI 2026
Version: 0.2.0
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

  OperationDetailOut is a separate, richer shape (not an extension of
  OperationOut) because:
  - hold_point_status requires a server-side scan of inspection history
  - predecessor_summaries requires a join on operation_predecessors
  - stop_requests requires a cross-table join on StopRequest
  Keeping the list shape (OperationOut) lighter avoids paying that
  enrichment cost on every list response.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import (
    HoldPointStatus,
    OpStatus,
    ScheduleConstraint,
    WOStatus,
)


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
    """Request body for PATCH /api/operations/{id} (draft WO only).

    `version` is optional so §6.3.1 If-Match may be used instead;
    the route reconciles via `resolve_version_assertion`.
    """

    version: int | None = None
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

    version: int | None = None


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


class PredecessorSummary(BaseModel):
    """Minimal predecessor data per UX spec v1.5.1 §5.4.8.

    Drives the disabled [Start Timer] tooltip ("Waiting for: Op N —
    description (status)") without requiring the client to fetch each
    predecessor in a follow-up call.  Field set is intentionally
    minimal so the response stays small for operations with many
    predecessors.

    `sequence_number` is the spec wire name; the underlying model
    column is named `sequence`.  The service layer maps that on
    construction so this schema stays spec-accurate without
    renaming the database column.
    """

    id: uuid.UUID
    sequence_number: int
    description: str
    status: OpStatus


class StopRequestSummary(BaseModel):
    """Pending stop-request summary per UX spec v1.5.1 §5.4.8 / §5.4.9.

    The operation-detail response serializes only `pending` rows
    here.  Fulfilled and expired entries live in the audit trail
    (§6.6) and are not returned on this read surface.

    `requested_by` is a nested object (user_id + display_name) so
    the engineer's Operation Detail view can render the amber
    indicator + operator attribution without a follow-up
    /api/users/{id} round trip.  The shape mirrors
    forge.schemas.stop_request.StopRequestActor — the inline dict
    typing here avoids a cross-schema import on the read path.
    """

    request_id: uuid.UUID
    labor_entry_id: uuid.UUID
    status: str
    requested_by: dict[str, str]
    note: str
    created_at: datetime


class OperationDetailOut(BaseModel):
    """Enriched operation detail response (UX spec v1.5.1 §5.4.8).

    Used by both the engineer Operation Detail page (§5.4.11) and the
    operator Operation Execution SSE reconnect contract (§6.5.2 — the
    client refetches this endpoint as the authoritative reconciliation
    surface on every reconnect, regardless of any best-effort SSE
    replay).

    `program_id` is required so the client auto-switch behavior
    (§3.2 / §6.7.4) can identify the entity's owning program after a
    deep-link fetch where the caller's currently-selected program may
    differ from the entity's program.

    `hold_point_status` is server-derived (spec §5.4.3 explicitly
    forbids client-side traversal of inspection history).  See the
    KNOWN CONTRACT MISMATCH note in
    forge.services.operation_service._derive_hold_point_status — the
    `conditionally_satisfied` value is exposed per spec but COMP-001
    completion enforcement still requires `accept`.

    `predecessor_summaries` carries enough data for the disabled
    [Start Timer] tooltip in one round-trip.  When the WO has any
    explicit predecessor edges, each op's summary is sourced from
    those edges (roots have empty `predecessors`); otherwise the
    summary falls back to default-sequential by `sequence`.

    `stop_requests` contains only PENDING rows — fulfilled / expired
    entries live in the audit trail (§6.6).
    """

    id: uuid.UUID
    work_order_id: uuid.UUID
    # Parent WO status — §6.5.2 reconnect contract needs it so the
    # client can render the work-order-on-hold banner (§5.3, §5.4.10)
    # and gate operation actions against parent state without a
    # second fetch of the parent WO.
    work_order_status: WOStatus
    program_id: uuid.UUID
    sequence_number: int
    description: str
    status: OpStatus
    execution_cycle: int
    inspection_hold: bool
    safety_critical: bool
    estimated_hours: float
    started_at: datetime | None
    completed_at: datetime | None
    hold_point_status: HoldPointStatus
    predecessors: list[uuid.UUID]
    predecessor_summaries: list[PredecessorSummary]
    stop_requests: list[StopRequestSummary]
    version: int
    created_at: datetime
    updated_at: datetime
