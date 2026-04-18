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
from forge.schemas.operation import OperationOut


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

    Spec §6.3.1 allows either `version` in the body OR an
    `If-Match` header for optimistic locking.  Making `version`
    optional here keeps the body contract lenient — the route
    resolves the effective expected version via
    forge.api.concurrency.effective_expected_version and rejects
    missing-both with 400 MISSING_VERSION, mismatched-both with
    409 VERSION_CONFLICT.  Callers still using body-only continue
    unchanged.
    """

    version: int | None = None
    engine_serial: str | None = None
    module: str | None = None
    build_phase: BuildPhase | None = None
    priority: Priority | None = None
    bom_revision_ref: str | None = None


class WorkOrderTransition(BaseModel):
    """Request body for state-transition POST endpoints.

    Same contract as WorkOrderUpdate: body-version is optional so
    an If-Match header alone satisfies §6.3.1.
    """

    version: int | None = None


class WorkOrderClose(BaseModel):
    """Request body for POST /api/work-orders/{id}/close (GC-4).

    Build spec §5.1: "body includes `version` and optional
    `closure_rationale`."  `version` follows the §6.3.1 If-Match
    flexibility (body-or-header) consistent with other state-
    transition endpoints; `closure_rationale` is the Phase-1
    attestation note persisted on the closed entity (UX spec §5.3).
    """

    version: int | None = None
    closure_rationale: str | None = Field(default=None, max_length=4000)


class WorkOrderHoldIn(BaseModel):
    """Request body for POST /api/work-orders/{id}/hold (UX spec v1.5.1
    §5.3 Hold Cascading).

    `version` is optional to support the §6.3.1 If-Match alternative
    (route-level `resolve_version_assertion` enforces exactly-one-of
    body.version or If-Match is present).  `force_stop_labor`
    defaults to False (administrative hold).  When True, the server
    atomically stops all active labor entries on child operations
    and emits one `labor.stopped_by_hold` audit event per stopped
    entry.  `reason` is currently nullable — the spec mentions a
    hold reason but the frozen contract (§5.3 Hold Cascading
    subsection) only binds it as future SSE payload data, so the
    field is accepted and stored in the audit before_state but not
    required.
    """

    version: int | None = None
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
    the [Fix Graph] / [Report Graph Issue] buttons.  The field is
    written by the graph validator at release + Fix Graph + Report
    Graph Issue; reads treat it as authoritative.
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
    # GC-4 closure-as-attestation (UX spec §5.3): the optional QA
    # rationale persisted at close time.  NULL on every WO that
    # has not yet been closed (and on legacy closed rows that
    # pre-date this column — see migration a8c2d4e5f6b7).
    closure_rationale: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClosureBlocker(BaseModel):
    """Single reason a WO close cannot proceed (GC-4).

    Codes match the 422 detail codes the close service raises so the
    pre-click tooltip and the post-click error speak the same
    vocabulary.  `entity_id` points at the offending NCR or
    operation when the blocker names a specific row; NULL for
    blockers that describe the WO as a whole.
    """

    code: str
    message: str
    entity_id: uuid.UUID | None = None


class WorkOrderDetailOut(WorkOrderOut):
    """Detail response for GET /api/work-orders/{id} (Build Spec §5.1).

    Why this shape: Build Spec §5.1 routes table documents the detail
    endpoint as "Get work order detail with operations", and §3.1
    WorkOrder class carries ``operations: list[Operation]`` in its
    relationships.  The list endpoint continues to return the lighter
    ``WorkOrderOut`` (no operations) so paginated responses stay small.
    The detail endpoint returns this subtype so the frontend work-order
    detail page (UX spec §5.3) can render the Operations tab without a
    second round trip.

    Operations are ordered by ``sequence`` ascending — matches the
    UX spec's operations table column order and avoids the client
    having to re-sort a paginated list.
    """

    operations: list[OperationOut] = Field(default_factory=list)
    # GC-4: server-derived close-readiness so the frontend can
    # disable the close button + render a precise blocker tooltip
    # WITHOUT a click probe.  `closure_ready` is True iff
    # `closure_blockers` is empty AND the WO is in `complete`.
    # Computed in `forge.services.work_order_service.
    # compute_closure_blockers`, which the close service ALSO
    # consults so the pre-click read and the post-click write
    # cannot drift.
    closure_ready: bool = False
    closure_blockers: list[ClosureBlocker] = Field(default_factory=list)

    model_config = {"from_attributes": True}
