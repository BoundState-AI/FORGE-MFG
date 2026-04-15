#!/usr/bin/env python3
"""
Module:   Labor Schemas
Purpose:  Pydantic request/response models for labor endpoints (spec §5.3).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  LaborCreate is minimal — started_at is set server-side on creation.
  LaborUpdate requires version for optimistic locking (spec §5.15).
  Permission enforcement (labor:update_own vs labor:update_any, spec
  §5.3) is handled at the service layer, not in the schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LaborVersionAssertions(BaseModel):
    """Optional §6.3.3 composite version assertions for labor handoff.

    UX spec v1.5.1 §5.4.2 names three assertable entities on the
    canonical labor-start endpoint:

      - target_operation:   the operation being handed off TO
                            (the op_id in the URL).
      - active_labor_entry: the user's currently-active labor entry
                            that will be implicitly stopped.
      - active_operation:   the operation that active labor entry
                            is on (the op being handed off FROM).

    Each field is optional — clients submit only the versions they
    observed.  A missing field means "no assertion."  When absent,
    the server runs its current default behavior; when present,
    every supplied assertion is checked and a single stale entry
    triggers a composite 409.
    """

    target_operation: int | None = None
    active_labor_entry: int | None = None
    active_operation: int | None = None


class LaborCreate(BaseModel):
    """Request body for POST /api/operations/{id}/labor.

    `version_assertions` is optional per UX spec §5.4.2 and §6.3.3;
    when present, the server validates each asserted version against
    the corresponding live entity and returns 409 on any mismatch.
    """

    role: str = Field(max_length=50)
    shift: str = Field(max_length=20)
    notes: str | None = None
    version_assertions: LaborVersionAssertions | None = None


class LaborUpdate(BaseModel):
    """Request body for PATCH /api/labor-entries/{id}.

    `version` is optional so an If-Match header can satisfy the
    §6.3.1 assertion instead (route reconciles via
    resolve_version_assertion).  Spec: labor:update_own allows
    updating own entries, labor:update_any allows updating any
    entry.
    """

    version: int | None = None
    stopped_at: datetime | None = None
    hours: float | None = None
    shift: str | None = None
    notes: str | None = None


class LaborOut(BaseModel):
    """Labor entry in API responses.

    `execution_cycle` is the parent operation's cycle at the time
    this labor row was created (build spec v1.4.2 GC-2).  Clients
    can group labor by cycle to report remediation hours
    separately from original-cycle hours.
    """

    id: uuid.UUID
    operation_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    execution_cycle: int
    started_at: datetime
    stopped_at: datetime | None
    hours: float
    shift: str
    notes: str | None
    version: int

    model_config = {"from_attributes": True}
