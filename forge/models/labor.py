#!/usr/bin/env python3
"""
Module:   Labor Model
Purpose:  LaborEntry — time tracking for operator work on operations.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Labor entries record who worked on what operation, for how long, and
  during which shift.  SOD-001 uses labor entries to verify that an
  inspector did not also perform work on the operation being inspected.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditableMixin, Base


class LaborEntry(AuditableMixin, Base):
    """Time-tracking record for operator work on an operation."""

    __tablename__ = "labor_entries"

    operation_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # Set to the parent operation's current execution_cycle at
    # creation time (build spec v1.4.2 GC-2).  After an NCR-driven
    # reopen increments op.execution_cycle, new labor rows carry the
    # new cycle so audit + reporting can distinguish remediation
    # effort from original-cycle effort.  Legacy rows that predate
    # this column are back-filled to 1 by the migration — every
    # production operation starts its first cycle at 1.
    execution_cycle: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    shift: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Relationships
    operation: Mapped[Operation] = relationship(  # noqa: F821
        back_populates="labor_entries"
    )
