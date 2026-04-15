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
    """Program summary for access-scoping display (build spec §5.0.3).

    Feature flags are exposed alongside identity fields because the
    frontend consumes them on every render path (UX spec v1.5.1 §1.4
    drives navigation visibility off schedule_enabled and oracle_access;
    §5.1 derives the dashboard overdue rule off overdue_threshold_hours).
    Embedding them in the standard Program payload removes a second
    round-trip and keeps the wire shape identical wherever a Program
    is returned (admin programs CRUD, user.program_memberships, etc.).

    `version` is exposed because admin program updates use the standard
    optimistic-locking contract (§6.3) — clients must echo the version
    they observed on the next PATCH so a concurrent edit by another
    admin produces a 409 instead of silently overwriting.
    """

    id: uuid.UUID
    code: str
    name: str
    active: bool
    schedule_enabled: bool
    oracle_access: bool
    overdue_threshold_hours: int
    version: int

    model_config = {"from_attributes": True}


class ProgramListItemOut(ProgramOut):
    """Program list item — extends ProgramOut with member_count.

    Admin Programs page (UX spec v1.5.1 §5.10) renders a list with
    "member count" alongside flags.  Computing the count once per row
    in the list service avoids N+1 follow-up queries when the admin
    has many programs.  The detail GET returns the lighter ProgramOut
    because the detail page does not display the count.
    """

    member_count: int


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
    """Request body for POST /api/admin/programs.

    Feature flags are optional on create and fall back to the model /
    DB defaults (schedule_enabled=true, oracle_access=false,
    overdue_threshold_hours=24).  This matches the rollout intent: a
    new program ships with schedule visible, chat opted out, and the
    standard 24-hour overdue threshold.
    """

    code: str = Field(max_length=50)
    name: str = Field(max_length=200)
    schedule_enabled: bool | None = None
    oracle_access: bool | None = None
    # Bound at 1..720 hours (1 hour to 30 days) — anything beyond a
    # month is functionally "never overdue" and almost certainly a
    # data-entry mistake on the admin form.
    overdue_threshold_hours: int | None = Field(default=None, ge=1, le=720)


class ProgramUpdate(BaseModel):
    """Request body for PATCH /api/admin/programs/{id}.

    All mutable fields are optional — admin can toggle a single flag
    without re-sending the whole record.  None means "leave unchanged"
    at the service layer (model_dump(exclude_unset=True) is the
    canonical pattern in admin_service).

    `version` is OPTIONAL on the wire: UX spec §6.3.1 permits either
    body-version OR an If-Match header.  The route reconciles via
    `resolve_version_assertion` and rejects missing-both with 400.
    Admin updates are typically composite (name + flags in one
    request) so body-version is still the ergonomic default, but
    tooling that already sets If-Match globally works without
    duplicating the assertion.
    """

    version: int | None = None
    name: str | None = None
    active: bool | None = None
    schedule_enabled: bool | None = None
    oracle_access: bool | None = None
    overdue_threshold_hours: int | None = Field(default=None, ge=1, le=720)


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
