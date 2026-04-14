#!/usr/bin/env python3
"""
Module:   Part Schemas
Purpose:  Pydantic request/response models for parts endpoints (spec §5.7).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  PartCreate requires a program_id so scope lands at creation time
  (unlike NCR, which inherits scope from its parent WO).  Status is
  controlled via PartUpdate which funnels through the transition map.
  The traceability response is a structured projection — the rule
  "full chain" is implemented in the service and returned here as a
  serializable dict of lists so the client sees the entire graph
  reachable from the part in a single round trip.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import PartStatus, TraceLinkType, TraceSourceType


class PartCreate(BaseModel):
    """Request body for POST /api/parts (creates in `raw`)."""

    serial_number: str = Field(max_length=100)
    part_number: str = Field(max_length=100)
    part_description: str = Field(max_length=500)
    program_id: uuid.UUID
    material_certification_id: uuid.UUID | None = None
    parent_assembly_id: uuid.UUID | None = None
    current_location: str | None = Field(default=None, max_length=200)


class PartUpdate(BaseModel):
    """Request body for PATCH /api/parts/{id}.

    Spec §5.15: version required on mutations.  status transitions
    are validated against VALID_PART_TRANSITIONS in the service.
    """

    version: int
    status: PartStatus | None = None
    current_location: str | None = Field(default=None, max_length=200)
    parent_assembly_id: uuid.UUID | None = None


class PartOut(BaseModel):
    """PartInstance in API responses."""

    id: uuid.UUID
    serial_number: str
    part_number: str
    part_description: str
    program_id: uuid.UUID
    status: PartStatus
    material_certification_id: uuid.UUID | None
    parent_assembly_id: uuid.UUID | None
    current_location: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TraceLinkOut(BaseModel):
    """A single traceability edge."""

    id: uuid.UUID
    source_type: TraceSourceType
    source_id: uuid.UUID
    target_type: TraceSourceType
    target_id: uuid.UUID
    link_type: TraceLinkType

    model_config = {"from_attributes": True}


class TraceabilityOut(BaseModel):
    """Full traceability chain for a part (spec §5.7).

    `assembly_chain` walks parent_assembly_id upward (current → parent →
    grandparent).  `child_parts` lists any direct children (parts whose
    parent_assembly_id is this part).  `links` is all TraceabilityLink
    rows where this part appears as either source or target.
    """

    part: PartOut
    material_certification_id: uuid.UUID | None
    assembly_chain: list[PartOut]
    child_parts: list[PartOut]
    links: list[TraceLinkOut]
