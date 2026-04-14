#!/usr/bin/env python3
"""
Module:   Admin Schemas
Purpose:  Request/response models for admin endpoints (spec §5.13).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Admin schemas back the user/role/program CRUD endpoints required by
  spec §5.13.  CLAUDE.md verification requires that this file exists
  and backs admin endpoint contracts.  Admin self-protection (cannot
  remove own admin:manage role or deactivate own account) is enforced
  at the service layer, not in schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class RoleOut(BaseModel):
    """Role with permission array for admin and auth display (spec §5.0.2)."""

    id: uuid.UUID
    name: str
    permissions: list[str]

    model_config = {"from_attributes": True}


class ProgramOut(BaseModel):
    """Program summary for access-scoping display (spec §5.0.3)."""

    id: uuid.UUID
    code: str
    name: str
    active: bool

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    """User detail with eagerly-embedded roles and program memberships.

    Roles and programs are embedded (not linked) because admin callers
    need the full permission set and access scope in a single response
    to render the user management UI without follow-up queries.

    is_system_actor is exposed so the admin UI can render it as
    read-only and prevent mutation attempts on the SYSTEM account
    (spec §3.1 — system actor records are immutable).
    """

    id: uuid.UUID
    cognito_sub: str
    email: str
    display_name: str
    active: bool
    # Exposed so the admin UI can prevent mutation on SYSTEM (spec §3.1)
    is_system_actor: bool
    roles: list[RoleOut] = []
    program_memberships: list[ProgramOut] = []

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    """Fields updatable via PATCH /api/admin/users/{id}.

    Only display_name and active are mutable via admin CRUD.
    email and cognito_sub are identity fields managed by Cognito.
    is_system_actor is immutable after provisioning (spec §3.1).
    Role and program assignments use dedicated endpoints.
    """

    display_name: str | None = None
    active: bool | None = None


class RoleAssign(BaseModel):
    """Request body for POST /api/admin/users/{id}/roles."""

    role_id: uuid.UUID


class ProgramAssign(BaseModel):
    """Request body for POST /api/admin/users/{id}/programs."""

    program_id: uuid.UUID


class ProgramCreate(BaseModel):
    """Request body for POST /api/admin/programs."""

    code: str = Field(max_length=50)
    name: str = Field(max_length=200)


class ProgramUpdate(BaseModel):
    """Request body for PATCH /api/admin/programs/{id}."""

    name: str | None = None
    active: bool | None = None


class DeadLetterOut(BaseModel):
    """Dead-letter delivery projection for the admin UI (spec §5.13).

    event_type and aggregate_* come from the DomainEvent row (resolved
    by the service).  attempt_count / last_error / last_attempt_at
    surface why the delivery ended up here so an operator can decide
    whether to retry or escalate.
    """

    delivery_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: str
    consumer_name: str
    attempt_count: int
    max_attempts: int
    last_error: str | None
    last_attempt_at: datetime | None

    model_config = {"from_attributes": True}
