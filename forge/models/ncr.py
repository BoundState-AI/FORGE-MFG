#!/usr/bin/env python3
"""
Module:   NCR Model
Purpose:  NonconformanceReport — quality deviation tracking and disposition.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  NCR lifecycle: open → dispositioned → closed, with voided only where
  the spec allows (spec §3.1).  SOD-002 prevents the initiator from
  dispositioning the same NCR.  initiated_by_id is the separation-of-duties
  anchor for that check.
"""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditableMixin, Base
from .enums import Disposition, NCRStatus


class NonconformanceReport(AuditableMixin, Base):
    """Quality nonconformance record with disposition workflow."""

    __tablename__ = "nonconformance_reports"

    ncr_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False
    )
    work_order_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    operation_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True
    )
    part_instance_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("part_instances.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(4000), nullable=False)
    root_cause: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    disposition: Mapped[Disposition] = mapped_column(
        default=Disposition.pending, nullable=False
    )
    disposition_rationale: Mapped[str | None] = mapped_column(
        String(4000), nullable=True
    )
    engineering_approval_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    mrb_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mrb_disposition: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    mrb_authority_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    schedule_impact_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    status: Mapped[NCRStatus] = mapped_column(
        default=NCRStatus.open, nullable=False
    )
    # SOD-002 anchor: initiator cannot disposition the same NCR
    initiated_by_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Structured evidence references (build spec v1.4.2 Decision 1).
    # Required non-empty for rework / repair dispositions at close time
    # — NCR close gate enforces this invariant in ncr_service.close_ncr.
    # Write paths hydrate ref metadata from evidence_confirmations so
    # clients cannot forge structured fields.
    evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
