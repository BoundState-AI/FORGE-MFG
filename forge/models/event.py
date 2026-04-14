#!/usr/bin/env python3
"""
Module:   Event Model
Purpose:  DomainEvent and DomainEventDelivery — transactional outbox.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Domain events use the transactional outbox pattern (spec §2.3):
  DomainEvent records are inserted in the same database transaction as
  the aggregate change.  A background worker polls using FOR UPDATE
  SKIP LOCKED and dispatches to handlers via DomainEventDelivery.

  DomainEvent rows may exist before the worker exists — do not invent
  a synchronous event-consumption shortcut (CLAUDE.md stage gate 4).

  DomainEventDelivery includes retry fields: attempt_count, max_attempts,
  next_attempt_at for exponential backoff, and available_at for delayed
  pickup scheduling.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .enums import DeliveryStatus, EventType


class DomainEvent(Base):
    """Transactional outbox event record.

    Inserted in the same transaction as the aggregate change.
    The id doubles as an idempotency key for event processing.
    """

    __tablename__ = "domain_events"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4
    )
    # Spec §4.5: persisted values must match canonical vocabulary
    # (WorkOrderCreated, NCRCreated, etc.), not Python member names.
    # values_callable ensures the .value (PascalCase) is stored.
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Per-aggregate ordering for deterministic replay
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)


class WormReplicationAttempt(Base):
    """Durable per-audit-row retry tracker for WORM replication (spec §7.2).

    The AuditRecord schema (spec §3.1) is deliberately minimal and
    append-only — the only fields the replicator mutates on the audit
    row itself are s3_replicated + s3_replicated_at.  Attempt state
    lives in this sidecar so a process restart cannot reset the
    max-attempts ceiling: if a row has been tried 9 times before a
    deploy, the next worker respects that history.

    Primary key is audit_record_id so an upsert by audit row is a
    single ON CONFLICT statement.
    """

    __tablename__ = "worm_replication_attempts"

    audit_record_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(String(4000), nullable=True)


class DomainEventDelivery(Base):
    """Delivery tracking for outbox consumer processing.

    The worker uses FOR UPDATE SKIP LOCKED to claim pending deliveries.
    Exponential backoff via next_attempt_at.  Dead-letter after
    max_attempts exhausted.
    """

    __tablename__ = "domain_event_deliveries"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4
    )
    event_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    consumer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(
        default=DeliveryStatus.pending, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
