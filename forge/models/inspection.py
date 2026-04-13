#!/usr/bin/env python3
"""
Module:   Inspection Model
Purpose:  InspectionEvent and SignOffEvent — quality gate records.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  InspectionEvents and SignOffEvents are keyed to execution_cycle so
  reworked operations require fresh inspections (COMP-001).  The
  SignOffEvent unique constraint on (operation_id, user_id, role,
  execution_cycle) prevents duplicate sign-offs per cycle.  SOD-001
  prevents inspectors who have labor entries from self-inspecting.
  SOD-005 requires distinct user_ids for dual sign-off on safety-critical
  operations.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditableMixin, Base
from .enums import InspectionResult, SignOffAction


class InspectionEvent(AuditableMixin, Base):
    """Quality inspection record tied to an operation's execution cycle."""

    __tablename__ = "inspection_events"

    operation_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    inspector_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Set to parent operation's current execution_cycle at creation time
    execution_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria: Mapped[str] = mapped_column(String(2000), nullable=False)
    result: Mapped[InspectionResult] = mapped_column(nullable=False)
    measurements: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    evidence_refs: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    inspected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Relationships
    operation: Mapped[Operation] = relationship(  # noqa: F821
        back_populates="inspection_events"
    )
class SignOffEvent(AuditableMixin, Base):
    """Role-based sign-off record tied to an operation's execution cycle.

    Unique constraint: one sign-off per (operation, user, role, cycle).
    SOD-005: safety-critical ops require distinct user_ids for dual sign-off.
    """

    __tablename__ = "sign_off_events"
    __table_args__ = (
        UniqueConstraint(
            "operation_id", "user_id", "role", "execution_cycle",
            name="uq_signoff_op_user_role_cycle",
        ),
    )

    operation_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    execution_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[SignOffAction] = mapped_column(nullable=False)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Relationships
    operation: Mapped[Operation] = relationship(  # noqa: F821
        back_populates="sign_off_events"
    )
