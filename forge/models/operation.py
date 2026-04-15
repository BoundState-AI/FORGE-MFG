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

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
)
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

# Self-referential M2M: explicit operation predecessor edges (UX spec
# §5.4.6).  One row per (successor, predecessor) pair.  Cross-WO edges
# are enforced at the app layer because FK cannot express "both ops
# share work_order_id" — the graph validator catches them as WO-REL-103
# (dangling) when the predecessor is not in the same WO's op set.  Self-
# reference is enforced here at the DB (CHECK constraint) so the
# validator is not the only gate.  ON DELETE CASCADE keeps the edge set
# self-consistent when a draft-WO op is deleted.
operation_predecessors = Table(
    "operation_predecessors",
    Base.metadata,
    Column(
        "operation_id",
        UUID(as_uuid=True),
        ForeignKey("operations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "predecessor_id",
        UUID(as_uuid=True),
        ForeignKey("operations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    CheckConstraint(
        "operation_id <> predecessor_id",
        name="ck_operation_predecessors_no_self_ref",
    ),
    extend_existing=True,
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
    # Per-cycle "currently in_progress since" timestamp (UX spec v1.5.1 §5.1).
    # Unlike started_at (set once, on first entry to in_progress), this field
    # resets on every rework → in_progress transition so the overdue clock
    # measures the CURRENT cycle's time under hold, not the operation's
    # original age.  Nullable because pending/unstarted ops have no active
    # cycle — the overdue computation treats a null active_since as "not
    # started" and returns is_overdue=False.
    active_since: Mapped[datetime | None] = mapped_column(
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
    # Explicit predecessor edges (UX spec §5.4.6).  `predecessors`
    # returns the operations that must complete before THIS op,
    # keyed through operation_predecessors.  `primaryjoin` /
    # `secondaryjoin` explicitly name which column anchors each
    # direction of the self-referential M2M.
    #
    # lazy="select" rather than "selectin": a self-referential M2M
    # under selectin can recurse unpredictably across the async-
    # session greenlet boundary (each loaded Operation triggers a
    # fresh selectin of its own predecessors).  The graph services
    # and release-time validator query the operation_predecessors
    # association table directly, so they do not rely on this
    # relationship being pre-loaded — it is here for the rare
    # callers that want a convenient walker and are willing to take
    # the per-access round trip.
    predecessors: Mapped[list[Operation]] = relationship(
        secondary=operation_predecessors,
        primaryjoin="Operation.id == operation_predecessors.c.operation_id",
        secondaryjoin="Operation.id == operation_predecessors.c.predecessor_id",
        lazy="select",
    )
