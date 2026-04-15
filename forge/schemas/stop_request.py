#!/usr/bin/env python3
"""
Module:   Stop Request Schemas
Purpose:  Request/response models for force-stop, stop-request, and
          flag-correction endpoints (UX spec v1.5.1 §5.4.9, §5.4.5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Each endpoint has its own typed shape rather than a shared
  union, because the semantics diverge materially:

    - ForceStopIn: engineer rationale (≥10 chars required) +
      labor_entry_version.  The rationale length floor matches
      the spec's "string (min 10 chars, required)" language.
    - StopRequestIn: operator note + labor_entry_version.
    - FlagCorrectionIn: operator note only.  No version check
      because flag-correction is an attention signal, not a
      state transition.

  StopRequestOut mirrors the spec wire shape verbatim (request_id,
  labor_entry_id, status, requested_by {user_id, display_name},
  note, created_at).  `requested_by` is a nested object rather
  than a flat user_id because the frontend needs display_name for
  the amber-indicator tooltip on the engineer's Operation Detail
  view and a second fetch per row would be expensive on a heavily-
  requested op.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import StopRequestStatus


class ForceStopIn(BaseModel):
    """Request body for POST /api/labor-entries/{id}/force-stop.

    `rationale` floor of 10 chars tracks UX spec §5.4.9 verbatim —
    short enough to catch accidental empty submits, long enough to
    force a one-phrase explanation for the audit trail.  The spec
    also shows labor_entry_version as required for optimistic
    locking (R5-R2-ISSUE-004).
    """

    rationale: str = Field(min_length=10, max_length=2000)
    labor_entry_version: int


class StopRequestIn(BaseModel):
    """Request body for POST /api/labor-entries/{id}/stop-request."""

    note: str = Field(min_length=1, max_length=2000)
    labor_entry_version: int


class FlagCorrectionIn(BaseModel):
    """Request body for POST /api/labor-entries/{id}/flag-correction.

    No version field — flag-correction does not transition state,
    it only emits an audit event for the engineer to notice on
    their next poll of the Work Order view.  Adding a version
    guard would add concurrent-edit failure modes with no
    corresponding correctness benefit.
    """

    note: str = Field(min_length=1, max_length=2000)


class StopRequestActor(BaseModel):
    """Nested `requested_by` object on StopRequestOut.

    Pre-resolves display_name so the frontend does not need a
    follow-up /api/users/{id} round trip to render the engineer's
    amber-indicator tooltip.
    """

    user_id: uuid.UUID
    display_name: str


class StopRequestOut(BaseModel):
    """Stop-request entity in API responses (UX spec §5.4.9).

    Wire-stable across the POST /stop-request success (201) and the
    operation-detail read embed (§5.4.8 stop_requests array).  Only
    `pending` rows appear in the default operation-detail response —
    fulfilled and expired live in the audit trail — but the schema
    carries the full lifecycle enum so a future admin read surface
    can return history without a schema fork.
    """

    request_id: uuid.UUID
    labor_entry_id: uuid.UUID
    status: StopRequestStatus
    requested_by: StopRequestActor
    note: str
    created_at: datetime
