#!/usr/bin/env python3
"""
Module:   Work Order Graph Schemas
Purpose:  Typed request/response models for the Fix Graph and Report
          Graph Issue endpoints (UX spec v1.5.1 §5.4.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Graph editing is a logically-parent-WO mutation, so the contract
  mirrors other version-gated WO transitions: the request carries
  the WO `version` for optimistic locking and a typed list of
  predecessor changes.  The response returns the updated WO plus
  every affected operation's current predecessor set so the
  frontend can rehydrate the graph view in a single response.

  Separate GraphPatchOut / GraphValidateOut rather than reusing a
  generic envelope because the two endpoints have different
  semantics:

    - PATCH /graph returns the mutated work order + operations on
      success; validation failures come back as a 422 via the
      existing error envelope (NOT inside this success schema).
    - POST /graph/validate returns `has_graph_issue` + the raw
      validation_errors list regardless of whether issues were
      found (the spec shows both the issues-found and
      no-issues-found 200 shapes).

  GraphValidationErrorOut is the wire shape of one error.  It
  mirrors forge.rules.graph_validation.GraphValidationError field
  for field so the service can project the validator's dataclass
  straight into the response without a second mapping layer.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from forge.schemas.operation import OperationDetailOut
from forge.schemas.work_order import WorkOrderOut


class PredecessorChange(BaseModel):
    """One add / remove edit to the predecessor graph.

    `action="add"` inserts a new edge (operation_id depends on
    predecessor_id); `action="remove"` deletes an existing edge.
    The service applies every change to a working copy before
    validating, so the order of changes within a request does not
    matter — the final edge set is what gets validated.

    Action is a Literal rather than an Enum so the OpenAPI client
    renders it as a string union without a separate enum type —
    matches the wire-example JSON in UX spec §5.4.6.
    """

    operation_id: uuid.UUID
    action: Literal["add", "remove"]
    predecessor_id: uuid.UUID


class GraphPatchIn(BaseModel):
    """Request body for PATCH /api/work-orders/{id}/graph.

    `version` is the WO's current version per §6.3 optimistic
    locking.  A stale value returns 409 VERSION_CONFLICT — same
    posture as every other WO state-transition endpoint.
    `predecessor_changes` is a batch: the server applies all
    edits atomically and validates the resulting graph; a single
    validation failure rolls back every edit in the batch.
    """

    version: int
    predecessor_changes: list[PredecessorChange] = Field(default_factory=list)


class GraphValidationErrorOut(BaseModel):
    """One validation error on the wire (UX spec §5.4.6).

    `code` is spec-stable (WO-REL-101/102/103).  `detail` is a
    human-readable sentence specific enough to name the offending
    operation(s) — the spec shows examples like
    "Cycle detected: Op 3 → Op 5 → Op 3".  `operation_ids` lets
    the frontend highlight the involved nodes without re-running
    any validation logic client-side.
    """

    code: str
    detail: str
    operation_ids: list[uuid.UUID] = Field(default_factory=list)


class GraphPatchOut(BaseModel):
    """Success response for PATCH /api/work-orders/{id}/graph.

    Returns the updated work order entity plus every operation in
    that WO with its current predecessor set.  Shipping the full
    op list (not just changed ones) means the client does not
    have to reconcile partial deltas against a stale local cache —
    rehydration from this payload is one round trip.
    """

    work_order: WorkOrderOut
    operations: list[OperationDetailOut]


class GraphValidateOut(BaseModel):
    """Response for POST /api/work-orders/{id}/graph/validate.

    Always 200.  `has_graph_issue` echoes the server's post-
    validation state on the WO; the frontend keys the button
    swap (Report Graph Issue → Fix Graph) off this field so the
    contract has a single source of truth.  `validation_errors`
    is the full list of failures (possibly empty).
    """

    has_graph_issue: bool
    validation_errors: list[GraphValidationErrorOut] = Field(
        default_factory=list,
    )
