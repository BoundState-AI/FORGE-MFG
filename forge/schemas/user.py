#!/usr/bin/env python3
"""
Module:   User Schemas
Purpose:  Pydantic response models for /api/users/me (spec §5.14).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why this design:
  /api/users/me returns the authenticated caller's own profile —
  identity, roles, programs.  We expose the live DB projection here
  (not the JWT claims) so the caller sees the authoritative view
  even if their token is within the 30-minute drift window.  The
  shape reuses RoleOut / ProgramOut from the admin schemas so the
  UI can render a consistent user card regardless of endpoint.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from forge.schemas.admin import ProgramOut, RoleOut


class UserMeOut(BaseModel):
    """Response body for GET /api/users/me."""

    id: uuid.UUID
    cognito_sub: str
    email: str
    display_name: str
    active: bool
    is_system_actor: bool
    roles: list[RoleOut] = Field(default_factory=list)
    program_memberships: list[ProgramOut] = Field(default_factory=list)
    # Derived: union of all assigned roles' permissions.
    permissions: list[str] = Field(default_factory=list)
    # GC-4: server-side closure enablement flag.  Frontend uses this
    # to decide whether to render the Close action affordances on
    # WorkOrderDetail + NCRDetail.  Sourced from `Settings.closure_enabled`
    # so it reflects the live governance posture, not user-specific
    # state.  Carried on `/api/users/me` (instead of a separate
    # config endpoint) because the frontend already loads this on
    # auth bootstrap — adds zero round-trips.
    closure_enabled: bool = False

    model_config = {"from_attributes": True}
