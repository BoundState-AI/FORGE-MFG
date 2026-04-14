#!/usr/bin/env python3
"""
Module:   Event Bus
Purpose:  Emit DomainEvent + DomainEventDelivery rows within the caller's
          database transaction (spec §2.3 transactional outbox, §4.5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Transactional outbox (spec §2.3): the DomainEvent row must be
  inserted in the same database transaction as the aggregate change
  it describes.  emit_event takes the caller's AsyncSession and
  adds both the event and its delivery row; the caller's existing
  commit publishes them atomically.

  emit_event is intentionally NOT async-I/O.  It does no network
  work — publishing happens in the worker, not in the request path.
  This is what keeps request latency decoupled from event delivery
  (spec §8.2) and keeps the outbox at-least-once without synchronous
  coupling.

  sequence_number is per-aggregate.  The spec requires "per-aggregate
  ordering for deterministic replay."  The original count+1 assignment
  raced under concurrent emits on the same aggregate: two writers
  could both read count=N and both insert sequence_number=N+1, forking
  per-aggregate ordering.  4B review fix:
    * _next_sequence now takes a per-aggregate
      pg_advisory_xact_lock(ns, hash(agg_type||':'||agg_id)) before
      counting, serializing emits per-aggregate while leaving
      unrelated aggregates free to run in parallel.  The lock auto-
      releases at commit/rollback.
    * Migration a3b83e24cdf1 adds a unique constraint on
      (aggregate_type, aggregate_id, sequence_number) — a concurrent
      emit that somehow slipped past the lock still fails at INSERT
      rather than silently duplicating.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models.enums import DeliveryStatus, EventType
from forge.models.event import DomainEvent, DomainEventDelivery

from .types import PRIMARY_CONSUMER

# Advisory-lock namespace for per-aggregate sequence allocation.
# The two-argument pg_advisory_xact_lock(int4, int4) takes two int32
# values, so this namespace must fit in int32.  Paired with a 32-bit
# hash of aggregate identity — different aggregates serialize against
# different locks so traffic on unrelated aggregates never blocks.
# Distinct from ADVISORY_LOCK_ID in the audit chain (which uses the
# single-argument int64 form) — no overlap is possible because the
# namespaces sit in different lock namespaces.
_AGGREGATE_SEQ_NAMESPACE = 87_350_002


async def _acquire_aggregate_lock(
    session: AsyncSession, aggregate_type: str, aggregate_id: str,
) -> None:
    """Take a per-aggregate advisory lock for the caller's transaction.

    pg_advisory_xact_lock auto-releases at commit/rollback, so we
    cannot leak a lock even if the caller raises between the lock
    acquisition and their commit.  The two-argument (int32, int32)
    form name-spaces the lock against unrelated consumers; the key
    is a 32-bit hash of the aggregate identity string, truncated
    into int32 range.
    """
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            ":ns, ("
            "  hashtextextended(:agg_type || ':' || :agg_id, 0)"
            "  & x'7fffffff'::bigint"
            ")::int)"
        ),
        {
            "ns": _AGGREGATE_SEQ_NAMESPACE,
            "agg_type": aggregate_type,
            "agg_id": aggregate_id,
        },
    )


async def _next_sequence(
    session: AsyncSession, aggregate_type: str, aggregate_id: str,
) -> int:
    """Return the next per-aggregate sequence_number.

    Why advisory-lock guarded: the previous count+1 implementation
    raced under concurrent emits on the same aggregate — two writers
    could both read count=N and both insert sequence_number=N+1.
    Taking an advisory lock on (aggregate_type, aggregate_id) before
    reading the count serializes emits per-aggregate while leaving
    unrelated aggregates untouched.  A unique constraint on
    (aggregate_type, aggregate_id, sequence_number) in migration
    a3b83e24cdf1 backs this up — a concurrent emit that somehow
    slips past the lock fails at INSERT rather than duplicating.
    """
    await _acquire_aggregate_lock(session, aggregate_type, aggregate_id)
    result = await session.execute(
        select(func.count(DomainEvent.id)).where(
            DomainEvent.aggregate_type == aggregate_type,
            DomainEvent.aggregate_id == aggregate_id,
        )
    )
    return int(result.scalar() or 0) + 1


async def emit_event(
    session: AsyncSession,
    *,
    event_type: EventType,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, object] | None = None,
) -> DomainEvent:
    """Emit a DomainEvent + its primary DomainEventDelivery row.

    State leaving: no event for this aggregate state.
    State entering: DomainEvent row + DomainEventDelivery(pending).
    Invariants:
      1. Caller owns the transaction; we only flush, not commit.
         Rollback of the caller's txn un-publishes the event — exactly
         what transactional outbox requires (spec §2.3).
      2. available_at defaults to "now" so the worker picks it up on
         the next poll.  Callers that want a delay should set it
         explicitly via a future argument (not needed yet).
    """
    now = datetime.now(UTC)
    event = DomainEvent(
        id=_uuid.uuid4(),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload or {},
        timestamp=now,
        sequence_number=await _next_sequence(
            session, aggregate_type, aggregate_id,
        ),
    )
    session.add(event)

    delivery = DomainEventDelivery(
        id=_uuid.uuid4(),
        event_id=event.id,
        consumer_name=PRIMARY_CONSUMER,
        status=DeliveryStatus.pending,
        attempt_count=0,
        max_attempts=10,
        available_at=now,
    )
    session.add(delivery)
    await session.flush()
    return event
