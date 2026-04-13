#!/usr/bin/env python3
"""
Module:   User Model
Purpose:  User and Role entities with M2M relationships.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Cognito authenticates; the application database authorizes (spec §5.0.4).
  User.roles and User.program_memberships are the system of record for
  role and program assignments.  The is_system_actor flag identifies the
  reserved SYSTEM user for background worker audit records — this user
  cannot authenticate via Cognito and is immutable after provisioning.

  Roles carry a JSON permissions array matching the spec §5.0.1 vocabulary.
  Effective permissions are the union of all assigned roles' permissions.

  Both User and Role inherit AuditableMixin per spec §3.1 ("All entities
  inherit from AuditableMixin").  User.created_by and User.updated_by are
  self-referential FKs — the SYSTEM actor seed self-references its own ID.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, ForeignKey, String, Table
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditableMixin, Base

if TYPE_CHECKING:
    from .program import Program

# --- Association tables ---
# Kept at module level so Alembic can discover them during autogenerate.

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id"), primary_key=True),
)

user_programs = Table(
    "user_programs",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True),
    Column(
        "program_id", UUID(as_uuid=True), ForeignKey("programs.id"), primary_key=True
    ),
)


class Role(AuditableMixin, Base):
    """Authorization role with permission strings (spec §5.0.1).

    Role names: operator, inspector, engineer, qa_manager,
    program_manager, admin.

    Permissions stored as JSON array per spec §3.1 Role definition:
    "permissions: list[str]  # JSON array of permission strings".
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    # Spec §3.1: "JSON array of permission strings (see Section 5.0)"
    permissions: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )


class User(AuditableMixin, Base):
    """Application user — may be a human operator or the SYSTEM actor.

    The SYSTEM actor (is_system_actor=True) is provisioned at startup
    for background worker audit records.  It cannot authenticate via
    Cognito and its record is immutable — enforced by a DB trigger
    (see seed migration).
    """

    __tablename__ = "users"

    cognito_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Phase 2 ITAR use — stored but NOT enforced in Phase 1
    clearance_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_system_actor: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # M2M relationships
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin")
    program_memberships: Mapped[list[Program]] = relationship(
        secondary=user_programs, lazy="selectin"
    )
