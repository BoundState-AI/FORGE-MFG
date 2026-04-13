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


class LaborCreate(BaseModel):
    """Request body for POST /api/operations/{id}/labor."""

    role: str = Field(max_length=50)
    shift: str = Field(max_length=20)
    notes: str | None = None


class LaborUpdate(BaseModel):
    """Request body for PATCH /api/labor/{id}.

    Spec: labor:update_own allows updating own entries,
    labor:update_any allows updating any entry.
    """

    version: int
    stopped_at: datetime | None = None
    hours: float | None = None
    shift: str | None = None
    notes: str | None = None


class LaborOut(BaseModel):
    """Labor entry in API responses."""

    id: uuid.UUID
    operation_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    started_at: datetime
    stopped_at: datetime | None
    hours: float
    shift: str
    notes: str | None
    version: int

    model_config = {"from_attributes": True}
