#!/usr/bin/env python3
"""
Module:   Operation Model
Purpose:  Operation entity — sequenced step within a work order.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Operations track execution_cycle for rework scenarios (spec §3.1).
  When an operation transitions rework → in_progress, execution_cycle
  increments.  Prior InspectionEvents and SignOffEvents remain for audit
  trail but only events matching the current execution_cycle satisfy
  hold enforcement (COMP-001) and sign-off completeness (COMP-003).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Table
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditableMixin, Base
from .enums import OpStatus, ScheduleConstraint

# M2M: operations ↔ part instances
op_part_instances = Table(
    "op_part_instances",
    Base.metadata,
    Column(
        "operation_id", UUID(as_uuid=True), ForeignKey("operations.id"),
        primary_key=True,
    ),
    Column(
        "part_instance_id", UUID(as_uuid=True), ForeignKey("part_instances.id"),
        primary_key=True,
    ),
)
class Operation(AuditableMixin, Base):
    """Sequenced manufacturing operation within a work order."""

    __tablename__ = "operations"

    work_order_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    work_instruction_ref: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controlled_document_refs.id"), nullable=True
    )
    tooling_required: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    inspection_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    safety_critical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sign_off_roles_required: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    estimated_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[OpStatus] = mapped_column(default=OpStatus.pending, nullable=False)
    # Starts at 1; incremented on each rework → in_progress transition
    execution_cycle: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Scheduling constraint
    constraint_type: Mapped[ScheduleConstraint] = mapped_column(
        default=ScheduleConstraint.none, nullable=False
    )
    constraint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    work_order: Mapped[WorkOrder] = relationship(  # noqa: F821
        back_populates="operations"
    )
    labor_entries: Mapped[list[LaborEntry]] = relationship(  # noqa: F821
        back_populates="operation", lazy="selectin"
    )
    inspection_events: Mapped[list[InspectionEvent]] = relationship(  # noqa: F821
        back_populates="operation", lazy="selectin"
    )
    sign_off_events: Mapped[list[SignOffEvent]] = relationship(  # noqa: F821
        back_populates="operation", lazy="selectin"
    )
    material_consumptions: Mapped[list[MaterialConsumption]] = relationship(  # noqa: F821
        back_populates="operation", lazy="selectin"
    )
    parts_installed: Mapped[list[PartInstance]] = relationship(  # noqa: F821
        secondary=op_part_instances, lazy="selectin"
    )
