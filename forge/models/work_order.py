#!/usr/bin/env python3
"""
Module:   Work Order Model
Purpose:  WorkOrder entity — central manufacturing execution record.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Work orders are the top-level execution unit.  Status transitions are
  enforced by the rules engine (spec §4.3), not here — the model only
  stores current state.  authored_by_id supports SOD-004 (author cannot
  release same WO).  drawing_refs and spec_refs are frozen at release
  per COMP-004.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditableMixin, Base
from .enums import BuildPhase, Priority, WOStatus

# --- Association tables for M2M document refs ---

wo_drawing_refs = Table(
    "wo_drawing_refs",
    Base.metadata,
    Column(
        "work_order_id", UUID(as_uuid=True), ForeignKey("work_orders.id"),
        primary_key=True,
    ),
    Column(
        "document_id", UUID(as_uuid=True),
        ForeignKey("controlled_document_refs.id"), primary_key=True,
    ),
)

wo_spec_refs = Table(
    "wo_spec_refs",
    Base.metadata,
    Column(
        "work_order_id", UUID(as_uuid=True), ForeignKey("work_orders.id"),
        primary_key=True,
    ),
    Column(
        "document_id", UUID(as_uuid=True),
        ForeignKey("controlled_document_refs.id"), primary_key=True,
    ),
)

wo_assigned_users = Table(
    "wo_assigned_users",
    Base.metadata,
    Column(
        "work_order_id", UUID(as_uuid=True), ForeignKey("work_orders.id"),
        primary_key=True,
    ),
    Column(
        "user_id", UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True,
    ),
)
class WorkOrder(AuditableMixin, Base):
    """Manufacturing work order with status lifecycle and program scoping."""

    __tablename__ = "work_orders"

    wo_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False
    )
    program_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    engine_serial: Mapped[str] = mapped_column(String(100), nullable=False)
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    build_phase: Mapped[BuildPhase] = mapped_column(nullable=False)
    status: Mapped[WOStatus] = mapped_column(
        default=WOStatus.draft, nullable=False
    )
    priority: Mapped[Priority] = mapped_column(
        default=Priority.routine, nullable=False
    )
    # SOD-004: author cannot release the same WO
    authored_by_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    bom_revision_ref: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_by: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # UX spec v1.5.1 §5.4.6: server-managed flag that gates the
    # [Fix Graph] / [Report Graph Issue] UI buttons.  Set true by
    # the DAG validator on release + Fix Graph failure; cleared on
    # successful Fix Graph.  `server_default='false'` lets raw-SQL
    # INSERTs in legacy test fixtures remain schema-compatible.
    has_graph_issue: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    # GC-4 closure-as-attestation (UX spec §5.3): optional rationale
    # captured at close time and persisted on the closed entity.  Part
    # of the attestation artifact alongside the actor + timestamp +
    # version + close audit row.  Nullable because Phase-1 makes the
    # rationale optional — the operational decision is the click-to-
    # close action itself; rationale is a free-form QA note when the
    # closer wants to record context.
    closure_rationale: Mapped[str | None] = mapped_column(
        String(4000), nullable=True,
    )

    # Relationships
    program: Mapped[Program] = relationship(lazy="selectin")  # noqa: F821
    assigned_users: Mapped[list[User]] = relationship(  # noqa: F821
        secondary=wo_assigned_users, lazy="selectin"
    )
    # COMP-004: frozen at release
    drawing_refs: Mapped[list[ControlledDocumentRef]] = relationship(  # noqa: F821
        secondary=wo_drawing_refs, lazy="selectin"
    )
    spec_refs: Mapped[list[ControlledDocumentRef]] = relationship(  # noqa: F821
        secondary=wo_spec_refs, lazy="selectin"
    )
    operations: Mapped[list[Operation]] = relationship(  # noqa: F821
        back_populates="work_order",
        order_by="Operation.sequence",
        lazy="selectin",
    )
