#!/usr/bin/env python3
"""
Module:   Audit Model
Purpose:  AuditRecord — tamper-evident, hash-chained audit trail.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why this design:
  Audit records are append-only and hash-chained (CLAUDE.md architectural
  rule).  Each record's hash covers ALL stored fields including actor
  identity, IP, and session — this ensures full tamper-evidence (spec §3.1
  hash coverage note).  The advisory lock in the audit logger serializes
  insertion to prevent hash-chain forks under concurrency.

  AuditRecord does NOT use AuditableMixin because it has its own
  identity and timestamp semantics (no version column, no updated_by).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .enums import ActorType


class AuditRecord(Base):
    """Hash-chained, append-only audit trail record.

    Created by the service layer inside the unit-of-work transaction.
    Background workers use the SYSTEM actor identity.
    """

    __tablename__ = "audit_records"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4
    )
    sequence_number: Mapped[int] = mapped_column(
        Integer, unique=True, nullable=False
    )
    event_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    actor_type: Mapped[ActorType] = mapped_column(nullable=False)
    user_id: Mapped[_uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    user_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    before_state: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    after_state: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Hash chain fields — previous_hash links to prior record's record_hash
    previous_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    record_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # WORM replication tracking
    s3_replicated: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    s3_replicated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
