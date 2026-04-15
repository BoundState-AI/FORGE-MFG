#!/usr/bin/env python3
"""
Module:   Inspection Schemas
Purpose:  Pydantic request/response models for inspection and sign-off
          endpoints plus the Inspection Queue read-model row
          (UX spec v1.5.1 §5.4, §5.5.5).

BoundState AI 2026
Version: 0.3.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Inspection and sign-off requests are minimal: the operation is in the
  URL, the actor is in the auth context, and the execution_cycle is
  derived server-side from the operation's current cycle.  The client
  only supplies the result, criteria, measurements, and notes.

  Keeping execution_cycle off the request body is a deliberate safety
  invariant: the client cannot forge a stale cycle to satisfy a hold
  that no longer applies.

  InspectionQueueRowOut is an explicit read-model — one row per
  inspection hold-point that currently has queue-relevant state —
  NOT a serialized InspectionEvent.  The frontend queue renders from
  this shape; raw events are still available via operation detail
  (§5.4.8).  The read-model keeps event-shape concerns out of queue
  UI code and lets the server own overdue / hold-point derivation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import (
    HoldPointStatus,
    InspectionQueueState,
    InspectionResult,
    OpStatus,
    SignOffAction,
)


class InspectionCreate(BaseModel):
    """Request body for POST /api/operations/{id}/inspect.

    `supersedes_inspection_id` opts into Resolve Conditional Finding
    mode (UX spec v1.5.1 §5.5.5 / R5-R2-ISSUE-006): the inspector
    is resolving a prior `conditional` inspection on the same
    operation.  Frontend derives this from the `?resolves=<id>`
    query parameter on the inspection route.  Absent / None means
    a normal first-time inspection.

    `evidence_refs` entries are structured submissions (build spec
    v1.4.2 Decision 1): only `s3_key` is load-bearing — the service
    hydrates the full ref metadata from the evidence_confirmations
    registry.  Bare-string refs are rejected at the service layer.
    """

    criteria: str = Field(max_length=2000)
    result: InspectionResult
    measurements: dict[str, object] = Field(default_factory=dict)
    # Accept both dicts and strings at the schema layer so the
    # service can emit the UI-facing EVIDENCE_FORMAT_INVALID code
    # (with the offending s3_key) instead of Pydantic's generic
    # dict_type error.  The service rejects bare strings and
    # hydrates dicts from the registry.
    evidence_refs: list[dict[str, object] | str] = Field(
        default_factory=list,
    )
    notes: str | None = Field(default=None, max_length=2000)
    supersedes_inspection_id: uuid.UUID | None = Field(default=None)


class InspectionOut(BaseModel):
    """Inspection event in API responses.

    `evidence_refs` echoes the persisted structured refs verbatim.
    Legacy rows written before the Decision-1 contract may surface
    as bare strings — the read path tolerates that shape; the
    Pydantic type here is a permissive list of objects OR strings
    so the response validates against every row already in the DB.
    """

    id: uuid.UUID
    operation_id: uuid.UUID
    inspector_id: uuid.UUID
    execution_cycle: int
    criteria: str
    result: InspectionResult
    measurements: dict[str, object]
    evidence_refs: list[dict[str, object] | str]
    inspected_at: datetime
    notes: str | None
    supersedes_inspection_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SignOffCreate(BaseModel):
    """Request body for POST /api/operations/{id}/sign-off."""

    role: str = Field(max_length=50)
    action: SignOffAction
    notes: str | None = Field(default=None, max_length=2000)


class SignOffOut(BaseModel):
    """Sign-off event in API responses."""

    id: uuid.UUID
    operation_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    execution_cycle: int
    action: SignOffAction
    signed_at: datetime
    notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InspectionQueueRowOut(BaseModel):
    """Single row in the Inspection Queue read-model (UX spec §5.5.5).

    Keyed by operation_id — one row per operation with queue-relevant
    hold-point state.  The row mixes the operation, its parent WO,
    and (for conditional rows) the latest current-cycle conditional
    inspection the inspector is being asked to resolve.

    Field notes:
    - program_id: pulled through the WO join; enables the §3.2 auto-
      switch behavior in the frontend when a cross-program deep-link
      lands on a conditional row.
    - state: the server-assigned queue bucket per §5.5.5 (pending /
      conditional / overdue / completed).  The frontend does not
      re-derive this.
    - hold_point_status: the §5.4.3 operation-level hold status; kept
      alongside `state` so queue rows carry the same vocabulary the
      operation detail surface uses (prevents drift between queue
      copy and detail page copy).
    - is_overdue / overdue_since: null when the op has not crossed
      the program's overdue_threshold_hours.  overdue_since is the
      moment the op became overdue, computed server-side from
      started_at + overdue_threshold_hours — stable across polls so
      "time overdue" can be rendered by the client.
    - conditional_inspection_id: present ONLY on state=conditional
      rows.  The frontend passes this as `?resolves=<id>` on the
      Inspection Form link (§5.5.5, R5-R2-ISSUE-006).  Null for all
      other states.
    - inspected_at: the timestamp of the inspection that drove the
      row's current hold_point_status (pending → null).
    """

    operation_id: uuid.UUID
    work_order_id: uuid.UUID
    program_id: uuid.UUID
    work_order_number: str
    sequence_number: int
    description: str
    operation_status: OpStatus
    hold_point_status: HoldPointStatus
    state: InspectionQueueState
    safety_critical: bool
    is_overdue: bool
    overdue_since: datetime | None
    conditional_inspection_id: uuid.UUID | None
    inspected_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": False}
