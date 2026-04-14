#!/usr/bin/env python3
"""
Module:   User Routes
Purpose:  HTTP endpoints for current-user introspection (spec §5.14).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  /api/users/me is the single self-introspection endpoint: any
  authenticated user may call it to read their profile, roles,
  programs, and the derived permission union.  We project the live
  DB state so the caller sees the authoritative view even if their
  access token is still within the 30-minute drift window
  (spec §5.0.4).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, get_auth_context
from forge.api.deps import get_db
from forge.schemas.admin import ProgramOut, RoleOut
from forge.schemas.user import UserMeOut
from forge.services import admin_service

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/me", response_model=UserMeOut)
async def get_me(
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db),
) -> UserMeOut:
    """Return the authenticated caller's live DB profile.

    Any authenticated user (no specific permission required per
    spec §5.14).  Returns roles + program memberships + the derived
    permission union so the UI does not need a second round-trip.
    """
    try:
        user_id = uuid.UUID(auth.sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Auth context has no valid user id",
        ) from exc

    user = await admin_service.get_user_self(session, user_id)
    return UserMeOut(
        id=user.id,
        cognito_sub=user.cognito_sub,
        email=user.email,
        display_name=user.display_name,
        active=user.active,
        is_system_actor=user.is_system_actor,
        roles=[RoleOut.model_validate(r) for r in user.roles],
        program_memberships=[
            ProgramOut.model_validate(p) for p in user.program_memberships
        ],
        permissions=admin_service.effective_permissions(user),
    )
