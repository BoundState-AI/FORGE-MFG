#!/usr/bin/env python3
"""
Module:   Dead-Letter Service
Purpose:  Admin-facing queries and retry mutation for DomainEventDelivery
          rows in dead_letter status (spec §5.13, §4.5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Dead letters are a read-only pane for operations: list the bad
  rows, show why each failed, and allow a manual retry.  The retry
  endpoint resets attempt_count and available_at so the worker
  picks the row up again on its next poll — no special-case
  handler path needed.

  Every entrypoint goes through the admin self-protection helper
  (_require_admin_manage) so "caller has admin:manage" is a
  live-DB decision (spec §5.0.4).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType, DeliveryStatus
from forge.models.event import DomainEvent, DomainEventDelivery
from forge.services.admin_service import _require_admin_manage


async def list_dead_letters(
    session: AsyncSession, *, actor_id: _uuid.UUID,
    page: int = 1, page_size: int = 50,
) -> tuple[list[dict[str, object]], int]:
    """Return paginated dead-letter projections.

    The projection joins DomainEventDelivery to DomainEvent so the
    admin UI gets event_type + aggregate_* without a second
    round-trip.  Live-DB admin check per spec §5.0.4.
    """
    await _require_admin_manage(session, actor_id)

    total_result = await session.execute(
        select(func.count(DomainEventDelivery.id)).where(
            DomainEventDelivery.status == DeliveryStatus.dead_letter,
        )
    )
    total = int(total_result.scalar() or 0)

    offset = (page - 1) * page_size
    stmt = (
        select(DomainEventDelivery, DomainEvent)
        .join(DomainEvent, DomainEvent.id == DomainEventDelivery.event_id)
        .where(DomainEventDelivery.status == DeliveryStatus.dead_letter)
        .order_by(DomainEventDelivery.last_attempt_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    items: list[dict[str, object]] = []
    for delivery, event in result.all():
        items.append({
            "delivery_id": delivery.id,
            "event_id": event.id,
            "event_type": event.event_type.value,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "consumer_name": delivery.consumer_name,
            "attempt_count": delivery.attempt_count,
            "max_attempts": delivery.max_attempts,
            "last_error": delivery.last_error,
            "last_attempt_at": delivery.last_attempt_at,
        })
    return items, total


async def _load_dead_letter(
    session: AsyncSession, delivery_id: _uuid.UUID,
) -> DomainEventDelivery:
    """Load a delivery and verify it is actually dead-lettered.

    Separated so retry_dead_letter stays under the 50-line Tier-1
    guideline and the precondition is re-usable if a future admin
    action (e.g., bulk retry) needs the same gate.
    """
    delivery = await session.get(DomainEventDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Dead letter not found")
    if delivery.status != DeliveryStatus.dead_letter:
        raise HTTPException(
            status_code=422,
            detail="Delivery is not in dead_letter status",
        )
    return delivery


async def retry_dead_letter(
    session: AsyncSession,
    delivery_id: _uuid.UUID,
    *,
    actor_id: _uuid.UUID,
) -> DomainEventDelivery:
    """Reset a dead-letter delivery so the worker picks it up again.

    State leaving: dead_letter.  State entering: pending.
    Invariants:
      1. Caller has admin:manage (live DB check, spec §5.0.4).
      2. Target row exists and is in dead_letter state (see
         _load_dead_letter).
      3. attempt_count is cleared to 0 so the backoff formula starts
         fresh; last_error is preserved for audit context.
      4. available_at bumped to now so the next poll claims the row.
    """
    await _require_admin_manage(session, actor_id)
    delivery = await _load_dead_letter(session, delivery_id)

    before = delivery.status.value
    now = datetime.now(UTC)
    delivery.status = DeliveryStatus.pending
    delivery.attempt_count = 0
    delivery.available_at = now
    delivery.next_attempt_at = None
    # last_error intentionally preserved so the admin can correlate
    # the reason the row was sent back for retry.
    await session.flush()

    await create_audit_record(
        session,
        event_id=_uuid.uuid4(),
        actor_type=ActorType.human,
        user_id=actor_id,
        user_role=None,
        action_type="admin.dead_letter.retry",
        entity_type="domain_event_delivery",
        entity_id=delivery.id,
        before_state={"status": before},
        after_state={"status": delivery.status.value},
    )
    return delivery
