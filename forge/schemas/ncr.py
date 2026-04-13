#!/usr/bin/env python3
"""
Module:   NCR Schemas
Purpose:  Pydantic request/response models for the NCR endpoints (spec §5.5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Create / Disposition / Close are separate models so each one carries
  exactly the fields that step requires — the disposition decision and
  rationale must be present on disposition, not on creation, so a
  partial NCR cannot accidentally short-circuit the workflow.
  initiated_by_id is server-derived from the auth context, never
  accepted from the client (spec §4.2 SOD-002 anchor).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import Disposition, NCRStatus


class NCRCreate(BaseModel):
    """Request body for POST /api/ncrs (creates in `open`).

    initiated_by_id is taken from the auth context server-side and is
    not accepted from the client — that is the SOD-002 anchor.
    """

    work_order_id: uuid.UUID
    operation_id: uuid.UUID | None = None
    part_instance_id: uuid.UUID | None = None
    description: str = Field(max_length=4000)
    mrb_required: bool = False


class NCRDisposition(BaseModel):
    """Request body for PATCH /api/ncrs/{id}/disposition (open → dispositioned).

    Spec §5.15: version is required.  SOD-002 (initiator independence)
    is enforced in the service layer, not here.
    """

    version: int
    disposition: Disposition
    disposition_rationale: str = Field(max_length=4000)
    engineering_approval_id: uuid.UUID | None = None
    mrb_disposition: str | None = Field(default=None, max_length=4000)
    mrb_authority_id: uuid.UUID | None = None
    schedule_impact_days: int | None = None


class NCRClose(BaseModel):
    """Request body for POST /api/ncrs/{id}/close (dispositioned → closed).

    Spec §5.15: version is required on state-transition endpoints.
    Optional root_cause may be recorded at closure for the audit trail.
    """

    version: int
    root_cause: str | None = Field(default=None, max_length=4000)


class NCROut(BaseModel):
    """NCR projected for API responses."""

    id: uuid.UUID
    ncr_number: str
    work_order_id: uuid.UUID
    operation_id: uuid.UUID | None
    part_instance_id: uuid.UUID | None
    description: str
    root_cause: str | None
    status: NCRStatus
    disposition: Disposition
    disposition_rationale: str | None
    engineering_approval_id: uuid.UUID | None
    mrb_required: bool
    mrb_disposition: str | None
    mrb_authority_id: uuid.UUID | None
    schedule_impact_days: int | None
    initiated_by_id: uuid.UUID
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
