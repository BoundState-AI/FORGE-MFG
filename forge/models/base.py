#!/usr/bin/env python3
"""
Module:   Base
Purpose:  SQLAlchemy declarative base and AuditableMixin for all domain models.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Every mutable entity carries a version column for optimistic locking
  (spec §5.15) and audit timestamps with actor tracking (spec §3.1).
  The version column starts at 1 and is incremented on every update;
  PATCH and state-transition endpoints check it to detect concurrent
  modification.  UUIDs are used for all primary keys to avoid sequential
  ID enumeration.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all FORGE-MFG ORM models."""

    pass


class AuditableMixin:
    """Mixin providing audit tracking and optimistic locking columns.

    Spec §3.1: all entities inherit created_at, created_by, updated_at,
    updated_by, and version.  The version column enforces optimistic
    locking — a version mismatch on update returns 409 Conflict.
    """

    # Primary key — UUID prevents sequential enumeration
    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_uuid.uuid4,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    @declared_attr
    def created_by(self) -> Mapped[_uuid.UUID]:
        """FK → User who created the record."""
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id"),
            nullable=False,
        )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @declared_attr
    def updated_by(self) -> Mapped[_uuid.UUID]:
        """FK → User who last updated the record."""
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id"),
            nullable=False,
        )

    # Optimistic lock counter — starts at 1, incremented on every update
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        nullable=False,
    )
