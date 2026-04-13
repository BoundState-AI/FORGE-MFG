#!/usr/bin/env python3
"""
Module:   Part Model
Purpose:  PartInstance and TraceabilityLink — serialized part tracking.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  PartInstance tracks individual serialized parts through the build
  process with self-referential parent_assembly_id for assembly hierarchy.
  TraceabilityLink provides a polymorphic graph connecting any two
  domain entities for full traceability chains (spec §5.7 GET
  /api/parts/{id}/traceability).
"""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditableMixin, Base
from .enums import PartStatus, TraceLinkType, TraceSourceType


class PartInstance(AuditableMixin, Base):
    """Individual serialized part with assembly hierarchy."""

    __tablename__ = "part_instances"

    serial_number: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    part_number: Mapped[str] = mapped_column(String(100), nullable=False)
    part_description: Mapped[str] = mapped_column(String(500), nullable=False)
    program_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    status: Mapped[PartStatus] = mapped_column(
        default=PartStatus.raw, nullable=False
    )
    material_certification_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_certifications.id"), nullable=True
    )
    # Self-referential: part installed into a parent assembly
    parent_assembly_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("part_instances.id"), nullable=True
    )
    current_location: Mapped[str | None] = mapped_column(String(200), nullable=True)


class TraceabilityLink(AuditableMixin, Base):
    """Polymorphic traceability edge connecting two domain entities."""

    __tablename__ = "traceability_links"

    source_type: Mapped[TraceSourceType] = mapped_column(nullable=False)
    source_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    target_type: Mapped[TraceSourceType] = mapped_column(nullable=False)
    target_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    link_type: Mapped[TraceLinkType] = mapped_column(nullable=False)
