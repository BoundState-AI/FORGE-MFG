#!/usr/bin/env python3
"""
Module:   Material Model
Purpose:  MaterialCertification and MaterialConsumption entities.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  MaterialCertification tracks lot/heat traceability and remaining quantity.
  MaterialConsumption records atomic balance decrements — the service layer
  must use SELECT ... FOR UPDATE to enforce quantity_remaining >= quantity
  (COMP-005 authoritative check, spec §4.4).  SOD-003 prevents the
  receiver from also accepting the material.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditableMixin, Base
from .enums import CertStatus


class MaterialCertification(AuditableMixin, Base):
    """Material lot certification with quantity tracking."""

    __tablename__ = "material_certifications"

    program_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    material_spec: Mapped[str] = mapped_column(String(200), nullable=False)
    lot_number: Mapped[str] = mapped_column(String(100), nullable=False)
    heat_number: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier: Mapped[str] = mapped_column(String(200), nullable=False)
    cert_document_ref_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("controlled_document_refs.id"), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # SOD-003 anchor: receiver cannot be acceptance inspector
    received_by_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    accepted_by_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    shelf_life_expiry: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[CertStatus] = mapped_column(
        default=CertStatus.quarantine, nullable=False
    )
    quantity_received: Mapped[float] = mapped_column(Float, nullable=False)
    quantity_remaining: Mapped[float] = mapped_column(Float, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(20), nullable=False)
class MaterialConsumption(AuditableMixin, Base):
    """Atomic material consumption record with lot balance snapshot.

    The lot_balance_after field captures the remaining quantity at the
    moment of consumption for auditability.  reversal_of_id links a
    reversal record back to the original consumption.
    """

    __tablename__ = "material_consumptions"

    certification_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_certifications.id"), nullable=False
    )
    work_order_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False
    )
    operation_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    part_instance_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("part_instances.id"), nullable=True
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(20), nullable=False)
    issued_by_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lot_balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    reversal_of_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_consumptions.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Relationships
    operation: Mapped[Operation] = relationship(  # noqa: F821
        back_populates="material_consumptions"
    )
