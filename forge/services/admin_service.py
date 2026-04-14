#!/usr/bin/env python3
"""
Module:   Admin Service
Purpose:  Business logic for admin user management — list, detail, update,
          role assign/remove, program assign/remove — and admin program
          CRUD — list (with member count), detail, create, update with
          optimistic concurrency (spec §5.13, UX spec v1.5.1 §5.10).

          Every user-level role or program mutation triggers an immediate
          Cognito attribute sync (spec §5.0.4); deactivation and admin-
          role removal additionally trigger a global sign-out.  Program
          CRUD does NOT trigger Cognito sync — programs do not appear
          in custom:programs claims directly; only user-program
          membership changes propagate to Cognito.

BoundState AI 2026
Version: 0.2.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Spec §5.0.4 lists "user deactivation checks, admin:manage permission
  verification, and any action on the admin's own account" as zero-drift
  actions — they must be decided on live DB state, not JWT claims.
  _block_self_destructive enforces this for both self-deactivation and
  self-admin-role removal.

  Cognito sync is mandatory, not best-effort (spec §5.13).  If the sync
  raises CognitoSyncError, we must roll back the DB transaction and
  surface 502 so we never land in a state where the DB says "no admin
  rights" but Cognito still issues a token that says otherwise.  The
  service re-raises; the route's transaction boundary (commit AFTER
  this function returns successfully) provides the rollback.

  Business logic stays here so a future caller that skips the route
  layer cannot bypass the self-protection or sync requirements.
"""

from __future__ import annotations

import logging
import uuid as _uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.config import Settings
from forge.models.enums import ActorType
from forge.models.program import Program
from forge.models.user import Role, User, user_programs
from forge.schemas.admin import ProgramCreate, ProgramUpdate, UserUpdate
from forge.services import cognito_sync
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)

logger = logging.getLogger(__name__)

# Spec §5.0.2: the admin role carries this permission.  Used by
# self-protection to identify "admin" roles without a name-literal
# comparison that could drift if the role name is ever renamed.
ADMIN_PERMISSION = "admin:manage"


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------


async def _get_user(session: AsyncSession, user_id: _uuid.UUID) -> User:
    """Load a user or 404.  Roles and programs are eager-loaded."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _get_role(session: AsyncSession, role_id: _uuid.UUID) -> Role:
    """Load a role or 404."""
    role = await session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


async def _get_program(
    session: AsyncSession, program_id: _uuid.UUID,
) -> Program:
    """Load a program or 404."""
    program = await session.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


async def _require_admin_manage(
    session: AsyncSession, actor_id: _uuid.UUID,
) -> None:
    """Enforce admin:manage based on live DB roles (spec §5.0.4)."""
    actor = await _get_user(session, actor_id)
    perms = set(effective_permissions(actor))
    if ADMIN_PERMISSION not in perms:
        raise HTTPException(
            status_code=403, detail="Admin permission required"
        )


# ---------------------------------------------------------------------------
# Self-protection (live DB — spec §5.0.4)
# ---------------------------------------------------------------------------


def _block_self_destructive(
    *, target: User, actor_id: _uuid.UUID, violation: str,
) -> None:
    """Raise 422 if an admin is attempting to kneecap their own account.

    Spec §5.0.4: these checks are zero-drift.  They run against live DB
    state (the User row passed in) and not against any JWT claim.
    """
    if target.id != actor_id:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "RULE_VIOLATION",
            "message": violation,
            "violations": [{
                "rule_id": "ADMIN-SELF-PROTECT",
                "rule_name": "Admin self-protection",
                "severity": "block",
                "message": violation,
                "context": {"user_id": str(actor_id)},
            }],
        },
    )


# ---------------------------------------------------------------------------
# Cognito sync wiring
# ---------------------------------------------------------------------------


def _cognito_sync_or_502(user: User, settings: Settings) -> None:
    """Push current roles + programs to Cognito or raise 502.

    Converts CognitoSyncError into an HTTPException so the route layer
    does not need to special-case it.  The caller has already mutated
    the user in the ORM session; if sync raises, the surrounding
    session.commit never runs and the session rolls back on context
    exit (spec §5.13: no partial commits).
    """
    try:
        cognito_sync.sync_user_attributes(
            settings=settings,
            cognito_sub=user.cognito_sub,
            roles=[r.name for r in user.roles],
            programs=[p.code for p in user.program_memberships],
        )
    except cognito_sync.CognitoSyncError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "COGNITO_SYNC_FAILED",
                "message": str(exc),
            },
        ) from exc


def _cognito_global_signout_or_502(user: User, settings: Settings) -> None:
    """Invalidate refresh tokens for a user or raise 502."""
    try:
        cognito_sync.global_sign_out(
            settings=settings, cognito_sub=user.cognito_sub,
        )
    except cognito_sync.CognitoSyncError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "COGNITO_SIGNOUT_FAILED",
                "message": str(exc),
            },
        ) from exc


# ---------------------------------------------------------------------------
# User read paths
# ---------------------------------------------------------------------------


async def get_user(
    session: AsyncSession, user_id: _uuid.UUID, actor_id: _uuid.UUID,
) -> User:
    """Admin read: load a single user with roles + programs."""
    await _require_admin_manage(session, actor_id)
    return await _get_user(session, user_id)


async def get_user_self(
    session: AsyncSession, user_id: _uuid.UUID,
) -> User:
    """Self read: load current user without admin permission."""
    return await _get_user(session, user_id)


async def list_users(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    actor_id: _uuid.UUID,
) -> tuple[list[User], int]:
    """Admin read: paginated user list.

    No program scope filter — admin:manage implies cross-program
    visibility per spec §5.0.2 ("admin: admin:manage, wo:read, ...").
    """
    await _require_admin_manage(session, actor_id)
    total_result = await session.execute(select(func.count(User.id)))
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(
        select(User)
        .order_by(User.display_name, User.id)
        .offset(offset)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


# ---------------------------------------------------------------------------
# User update (active, display_name) — self-protection on deactivation
# ---------------------------------------------------------------------------


async def update_user(
    session: AsyncSession,
    user_id: _uuid.UUID,
    data: UserUpdate,
    actor_id: _uuid.UUID,
    settings: Settings,
) -> User:
    """Admin update: display_name and active only.

    State leaving: current active.  State entering: data.active (if set).
    Invariants:
      1. Target user exists.
      2. SYSTEM actor cannot be mutated (spec §3.1).  Enforced by DB
         trigger; we fail fast here with a clear error rather than let
         the trigger raise deep in the stack.
      3. Self-deactivation is blocked (§5.0.4 zero-drift rule).
      4. Deactivation triggers global sign-out (spec §5.0.4).  If sign-
         out fails, the DB change is rolled back.
    """
    await _require_admin_manage(session, actor_id)
    target = await _get_user(session, user_id)
    if target.is_system_actor:
        raise HTTPException(
            status_code=422, detail="SYSTEM actor is immutable",
        )

    was_active = target.active
    if data.display_name is not None:
        target.display_name = data.display_name
    if data.active is not None:
        if data.active is False:
            _block_self_destructive(
                target=target, actor_id=actor_id,
                violation="An admin cannot deactivate their own account",
            )
        target.active = data.active

    target.updated_by = actor_id
    await session.flush()

    deactivation = was_active is True and data.active is False
    if deactivation:
        _cognito_global_signout_or_502(target, settings)

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.user.update",
        entity_type="user",
        entity_id=target.id,
        before_state={"active": was_active},
        after_state={"active": target.active},
    )
    return target


# ---------------------------------------------------------------------------
# Role assign / remove
# ---------------------------------------------------------------------------


async def assign_role(
    session: AsyncSession,
    user_id: _uuid.UUID,
    role_id: _uuid.UUID,
    actor_id: _uuid.UUID,
    settings: Settings,
) -> User:
    """Add a role to a user; sync Cognito attributes.

    State leaving: current role set.  State entering: current union {role}.
    Invariants:
      1. User and role exist.
      2. SYSTEM actor is immutable.
      3. Idempotent: re-assigning an already-held role is a no-op.
      4. Cognito sync must succeed before commit.
    """
    await _require_admin_manage(session, actor_id)
    target = await _get_user(session, user_id)
    if target.is_system_actor:
        raise HTTPException(
            status_code=422, detail="SYSTEM actor is immutable",
        )
    role = await _get_role(session, role_id)

    if role in target.roles:
        return target  # idempotent

    target.roles.append(role)
    target.updated_by = actor_id
    await session.flush()

    _cognito_sync_or_502(target, settings)

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.user.role.assign",
        entity_type="user",
        entity_id=target.id,
        after_state={"role_id": str(role.id), "role_name": role.name},
    )
    return target


async def remove_role(
    session: AsyncSession,
    user_id: _uuid.UUID,
    role_id: _uuid.UUID,
    actor_id: _uuid.UUID,
    settings: Settings,
) -> User:
    """Remove a role from a user; sync Cognito + possibly global-sign-out.

    State leaving: current role set.  State entering: current \\ {role}.
    Invariants:
      1. User and role exist.
      2. SYSTEM actor is immutable.
      3. Self-protection: an admin cannot remove their own admin:manage
         role (zero-drift live-DB check per §5.0.4).  We evaluate this
         *before* mutating the set so the check sees the current state.
      4. Cognito sync after removal.
      5. If the removed role carried admin:manage, call global sign-out
         to end any live sessions that relied on those claims
         (§5.0.4 emergency revocation).
    """
    await _require_admin_manage(session, actor_id)
    target = await _get_user(session, user_id)
    if target.is_system_actor:
        raise HTTPException(
            status_code=422, detail="SYSTEM actor is immutable",
        )
    role = await _get_role(session, role_id)

    if role not in target.roles:
        return target  # idempotent

    # Self-protection: admin cannot strip their own admin:manage.
    # Evaluate on the role being removed (live DB data), not a claim.
    role_is_admin = ADMIN_PERMISSION in (role.permissions or [])
    if role_is_admin:
        _block_self_destructive(
            target=target, actor_id=actor_id,
            violation="An admin cannot remove their own admin:manage role",
        )

    target.roles.remove(role)
    target.updated_by = actor_id
    await session.flush()

    _cognito_sync_or_502(target, settings)

    # Emergency revocation path: admin-level role lost → global sign-out
    # so any active sessions that relied on those claims cannot outlive
    # the DB change by the refresh-token horizon.
    if role_is_admin:
        _cognito_global_signout_or_502(target, settings)

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.user.role.remove",
        entity_type="user",
        entity_id=target.id,
        after_state={"role_id": str(role.id), "role_name": role.name},
    )
    return target


# ---------------------------------------------------------------------------
# Program assign / remove
# ---------------------------------------------------------------------------


async def add_program(
    session: AsyncSession,
    user_id: _uuid.UUID,
    program_id: _uuid.UUID,
    actor_id: _uuid.UUID,
    settings: Settings,
) -> User:
    """Add a program membership to a user; sync Cognito.

    State leaving: current program set.
    State entering: current union {program}.
    Invariants: user exists, program exists, SYSTEM immutable,
    idempotent re-add, Cognito sync succeeds.
    """
    await _require_admin_manage(session, actor_id)
    target = await _get_user(session, user_id)
    if target.is_system_actor:
        raise HTTPException(
            status_code=422, detail="SYSTEM actor is immutable",
        )
    program = await _get_program(session, program_id)

    if program in target.program_memberships:
        return target

    target.program_memberships.append(program)
    target.updated_by = actor_id
    await session.flush()

    _cognito_sync_or_502(target, settings)

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.user.program.add",
        entity_type="user",
        entity_id=target.id,
        after_state={
            "program_id": str(program.id), "program_code": program.code,
        },
    )
    return target


async def remove_program(
    session: AsyncSession,
    user_id: _uuid.UUID,
    program_id: _uuid.UUID,
    actor_id: _uuid.UUID,
    settings: Settings,
) -> User:
    """Remove a program membership; sync Cognito.

    State leaving: current program set.
    State entering: current \\ {program}.
    No self-protection gate here: program scope does not grant
    admin authority (spec §5.0.2).
    """
    await _require_admin_manage(session, actor_id)
    target = await _get_user(session, user_id)
    if target.is_system_actor:
        raise HTTPException(
            status_code=422, detail="SYSTEM actor is immutable",
        )
    program = await _get_program(session, program_id)

    if program not in target.program_memberships:
        return target

    target.program_memberships.remove(program)
    target.updated_by = actor_id
    await session.flush()

    _cognito_sync_or_502(target, settings)

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.user.program.remove",
        entity_type="user",
        entity_id=target.id,
        after_state={
            "program_id": str(program.id), "program_code": program.code,
        },
    )
    return target


# ---------------------------------------------------------------------------
# Helpers used by routes for response-building
# ---------------------------------------------------------------------------


def effective_permissions(user: User) -> list[str]:
    """Union of all role permissions for a user, sorted for stable output."""
    perms: set[str] = set()
    for role in user.roles:
        perms |= set(role.permissions or [])
    return sorted(perms)


# ---------------------------------------------------------------------------
# Programs CRUD (UX spec v1.5.1 §5.10 admin Programs surface)
# ---------------------------------------------------------------------------
#
# Programs CRUD lives in admin_service so it shares the
# `_require_admin_manage` zero-drift guard with the user surface.
# Cognito sync is NOT triggered by program CRUD — programs do not
# appear in custom:programs claims directly; user-program membership
# changes (add_program / remove_program above) are what propagate to
# Cognito.  Renaming a program or toggling its active flag does not
# change any user's claim set.


async def list_programs(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    actor_id: _uuid.UUID,
) -> tuple[list[tuple[Program, int]], int]:
    """Admin read: paginated program list with per-row member count.

    Returns (rows, total) where each row is (Program, member_count).
    The member_count is computed via a single LEFT JOIN aggregate so
    a program with N members costs one row, not N+1 lookups.

    No program-scope filter — admin:manage implies cross-program
    visibility (build spec §5.0.2).  The list is sorted by code so
    the admin sees a stable A→Z ordering regardless of insertion
    order.
    """
    await _require_admin_manage(session, actor_id)

    total_result = await session.execute(select(func.count(Program.id)))
    total = total_result.scalar() or 0

    member_count_col = func.count(user_programs.c.user_id).label("member_count")
    offset = (page - 1) * page_size
    stmt = (
        select(Program, member_count_col)
        .outerjoin(user_programs, user_programs.c.program_id == Program.id)
        .group_by(Program.id)
        .order_by(Program.code)
        .offset(offset)
        .limit(page_size)
    )
    rows = list((await session.execute(stmt)).all())
    return [(row[0], int(row[1])) for row in rows], total


async def get_program(
    session: AsyncSession, program_id: _uuid.UUID, actor_id: _uuid.UUID,
) -> Program:
    """Admin read: load a single program detail."""
    await _require_admin_manage(session, actor_id)
    return await _get_program(session, program_id)


async def create_program(
    session: AsyncSession,
    data: ProgramCreate,
    actor_id: _uuid.UUID,
) -> Program:
    """Create a new program with optional feature-flag overrides.

    State leaving: (program does not exist).
    State entering: (program exists, active=True).
    Invariant: caller has admin:manage; the unique constraint on
    `code` rejects duplicates at the DB level.

    Feature flags omitted from the body fall back to model server
    defaults (schedule_enabled=true, oracle_access=false,
    overdue_threshold_hours=24) — set during the slice 5B-1 program
    flag migration.
    """
    await _require_admin_manage(session, actor_id)

    program = Program(
        code=data.code,
        name=data.name,
        active=True,
        created_by=actor_id,
        updated_by=actor_id,
    )
    # Apply any optional flag overrides.  Pydantic exposes
    # model_fields_set so we can distinguish "not provided" from
    # "explicitly set to default".
    if "schedule_enabled" in data.model_fields_set:
        program.schedule_enabled = bool(data.schedule_enabled)
    if "oracle_access" in data.model_fields_set:
        program.oracle_access = bool(data.oracle_access)
    if "overdue_threshold_hours" in data.model_fields_set:
        program.overdue_threshold_hours = int(
            data.overdue_threshold_hours or 24,
        )

    session.add(program)
    await session.flush()
    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.program.create",
        entity_type="program",
        entity_id=program.id,
        after_state={
            "code": program.code,
            "name": program.name,
            "active": program.active,
            "schedule_enabled": program.schedule_enabled,
            "oracle_access": program.oracle_access,
            "overdue_threshold_hours": program.overdue_threshold_hours,
        },
    )
    return program


async def update_program(
    session: AsyncSession,
    program_id: _uuid.UUID,
    data: ProgramUpdate,
    actor_id: _uuid.UUID,
) -> Program:
    """Update mutable program fields with optimistic concurrency.

    State leaving: (current name/active/flags).
    State entering: (data-supplied name/active/flags).
    Invariant: caller has admin:manage; the version assertion catches
    a concurrent admin edit and produces 409 with the standard
    envelope (UX spec §6.3.2).

    `code` is intentionally immutable — the program code is the
    stable identifier referenced by Cognito custom:programs claims
    and renaming it would orphan every user's existing membership.
    """
    await _require_admin_manage(session, actor_id)
    program = await _get_program(session, program_id)

    try:
        check_version(program, data.version, entity_type="Program")
    except VersionConflictError as exc:
        raise version_conflict_response(exc) from exc

    before_state = {
        "name": program.name,
        "active": program.active,
        "schedule_enabled": program.schedule_enabled,
        "oracle_access": program.oracle_access,
        "overdue_threshold_hours": program.overdue_threshold_hours,
    }

    # exclude_unset preserves the "leave unchanged" semantics on
    # any field the client did not supply.
    payload = data.model_dump(exclude_unset=True, exclude={"version"})
    for field_name, value in payload.items():
        setattr(program, field_name, value)

    program.updated_by = actor_id
    increment_version(program)
    await session.flush()

    after_state = {
        "name": program.name,
        "active": program.active,
        "schedule_enabled": program.schedule_enabled,
        "oracle_access": program.oracle_access,
        "overdue_threshold_hours": program.overdue_threshold_hours,
    }
    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.program.update",
        entity_type="program",
        entity_id=program.id,
        before_state=before_state,
        after_state=after_state,
    )
    return program
