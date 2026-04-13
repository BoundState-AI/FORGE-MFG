#!/usr/bin/env python3
"""
Module:   Inspection Schemas
Purpose:  Pydantic request/response models for inspection and sign-off
          endpoints (spec §5.4).

BoundState AI 2026
Version: 0.1.0
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
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import InspectionResult, SignOffAction


class InspectionCreate(BaseModel):
    """Request body for POST /api/operations/{id}/inspect."""

    criteria: str = Field(max_length=2000)
    result: InspectionResult
    measurements: dict[str, object] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000)


class InspectionOut(BaseModel):
    """Inspection event in API responses."""

    id: uuid.UUID
    operation_id: uuid.UUID
    inspector_id: uuid.UUID
    execution_cycle: int
    criteria: str
    result: InspectionResult
    measurements: dict[str, object]
    evidence_refs: list[str]
    inspected_at: datetime
    notes: str | None
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
