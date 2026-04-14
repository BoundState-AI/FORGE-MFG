#!/usr/bin/env python3
"""
Module:   Admin Routes
Purpose:  HTTP endpoints for admin user management (spec §5.13).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Thin routes per CLAUDE.md.  Self-protection, Cognito sync, and
  audit are enforced in admin_service.  Every mutating route
  declares admin:manage and forwards settings + the caller's
  sub (for self-protection checks).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.auth import AuthContext, require_permission
from forge.api.deps import get_config, get_db
from forge.config import Settings
from forge.schemas.admin import (
    ProgramAssign,
    RoleAssign,
    UserOut,
    UserUpdate,
)
from forge.services import admin_service

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


@router.get("", response_model=dict[str, object])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List users (paginated)."""
    users, total = await admin_service.list_users(
        session, page=page, page_size=page_size,
        actor_id=uuid.UUID(auth.sub),
    )
    return {
        "items": [UserOut.model_validate(u) for u in users],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total > 0 else 0,
    }


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
) -> UserOut:
    """Get a user with roles and programs."""
    user = await admin_service.get_user(
        session, user_id, actor_id=uuid.UUID(auth.sub),
    )
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> UserOut:
    """Update a user (active, display_name).

    Deactivation triggers global sign-out via cognito_sync
    (spec §5.0.4).  Self-deactivation is blocked by the service.
    """
    user = await admin_service.update_user(
        session, user_id, data, uuid.UUID(auth.sub), settings,
    )
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/roles", response_model=UserOut, status_code=201,
)
async def assign_role(
    user_id: uuid.UUID,
    data: RoleAssign,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> UserOut:
    """Assign a role to a user; syncs Cognito attributes."""
    user = await admin_service.assign_role(
        session, user_id, data.role_id, uuid.UUID(auth.sub), settings,
    )
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.delete(
    "/{user_id}/roles/{role_id}", response_model=UserOut,
)
async def remove_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> UserOut:
    """Remove a role; syncs Cognito and global-signs-out if the role
    granted admin:manage (§5.0.4)."""
    user = await admin_service.remove_role(
        session, user_id, role_id, uuid.UUID(auth.sub), settings,
    )
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.post(
    "/{user_id}/programs", response_model=UserOut, status_code=201,
)
async def add_program(
    user_id: uuid.UUID,
    data: ProgramAssign,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> UserOut:
    """Add a program membership; syncs Cognito attributes."""
    user = await admin_service.add_program(
        session, user_id, data.program_id, uuid.UUID(auth.sub), settings,
    )
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.delete(
    "/{user_id}/programs/{program_id}", response_model=UserOut,
)
async def remove_program(
    user_id: uuid.UUID,
    program_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("admin:manage")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> UserOut:
    """Remove a program membership; syncs Cognito attributes."""
    user = await admin_service.remove_program(
        session, user_id, program_id, uuid.UUID(auth.sub), settings,
    )
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)
