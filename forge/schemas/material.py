#!/usr/bin/env python3
"""
Module:   Material Schemas
Purpose:  Pydantic request/response models for material endpoints (spec §5.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Receive / Accept / Consume each carry only the fields that step
  requires.  Receiver identity is server-derived (SOD-003 anchor).
  Consume always requires an operation_id so the consumption lands in
  the audit trail of a specific operation — not a loose charge against
  a cert.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from forge.models.enums import CertStatus


class MaterialReceive(BaseModel):
    """Request body for POST /api/material-certs (receive a new lot).

    received_by_id is taken from the auth context server-side and is
    not accepted from the client — SOD-003 anchor.
    """

    program_id: uuid.UUID
    material_spec: str = Field(max_length=200)
    lot_number: str = Field(max_length=100)
    heat_number: str = Field(max_length=100)
    supplier: str = Field(max_length=200)
    cert_document_ref_id: uuid.UUID
    quantity_received: float = Field(gt=0)
    unit_of_measure: str = Field(max_length=20)
    shelf_life_expiry: datetime | None = None


class MaterialAccept(BaseModel):
    """Request body for PATCH /api/material-certs/{id}/accept.

    Spec §5.15 requires version on state transitions.  SOD-003 enforced
    in service.
    """

    version: int


class MaterialConsume(BaseModel):
    """Request body for POST /api/material-certs/{id}/consume.

    operation_id is required — consumption is always against a specific
    operation (spec §3.1 MaterialConsumption model).  quantity must be
    positive; the atomic balance check is in the service layer.

    version is the cert's optimistic-lock version (spec §5.15).  We
    version the cert, not the consumption, because consume is the
    write that mutates cert.quantity_remaining — two concurrent consumes
    race on the cert row, and the version check pairs with SELECT FOR
    UPDATE to turn a lost-update conflict into a clean 409 instead of
    silently over-drawing.
    """

    version: int
    work_order_id: uuid.UUID
    operation_id: uuid.UUID
    part_instance_id: uuid.UUID | None = None
    quantity: float = Field(gt=0)
    unit_of_measure: str = Field(max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class MaterialCertOut(BaseModel):
    """MaterialCertification in API responses."""

    id: uuid.UUID
    program_id: uuid.UUID
    material_spec: str
    lot_number: str
    heat_number: str
    supplier: str
    cert_document_ref_id: uuid.UUID
    received_at: datetime
    received_by_id: uuid.UUID
    accepted_by_id: uuid.UUID | None
    accepted_at: datetime | None
    shelf_life_expiry: datetime | None
    status: CertStatus
    quantity_received: float
    quantity_remaining: float
    unit_of_measure: str
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MaterialConsumptionOut(BaseModel):
    """MaterialConsumption in API responses."""

    id: uuid.UUID
    certification_id: uuid.UUID
    work_order_id: uuid.UUID
    operation_id: uuid.UUID
    part_instance_id: uuid.UUID | None
    quantity: float
    unit_of_measure: str
    issued_by_id: uuid.UUID
    issued_at: datetime
    lot_balance_after: float
    notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
