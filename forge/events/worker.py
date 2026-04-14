#!/usr/bin/env python3
"""
Module:   Outbox Worker
Purpose:  Poll DomainEventDelivery rows with FOR UPDATE SKIP LOCKED,
          dispatch to handlers, and apply exponential-backoff retry or
          dead-letter on failure (spec §2.3, §4.5, §8.2).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  FOR UPDATE SKIP LOCKED is the standard Postgres pattern for
  multi-worker, at-least-once dispatch.  Each poll iteration claims a
  batch of pending deliveries in one transaction; sibling workers see
  those rows as locked and skip to the next.  Handlers run inside
  that same transaction so their DB mutations and the delivery-row
  mark-delivered commit atomically — partial progress is impossible
  (spec §2.3 transactional outbox).

  Backoff policy: attempt N waits min(base * 2**(N-1), cap).  On
  attempt_count reaching max_attempts, the delivery transitions to
  DeliveryStatus.dead_letter and stays visible via the admin
  dead-letter endpoints (spec §5.13).  available_at is bumped to
  next_attempt_at so the next poll naturally skips rows that are not
  ready yet.

  process_once is exposed separately from the run-forever loop so
  tests can drive a deterministic single tick without spawning a
  long-running task.  That keeps the loop trivial and the test
  surface focused on the claim/dispatch/retry logic.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import Settings, get_settings
from forge.db import get_session
from forge.models.enums import DeliveryStatus
from forge.models.event import DomainEvent, DomainEventDelivery

from .types import PRIMARY_CONSUMER, handler_for

logger = logging.getLogger(__name__)


def _compute_backoff(
    attempt: int, settings: Settings,
) -> timedelta:
    """Backoff for retry N.  min(base * 2**(N-1), cap) seconds.

    The +1-indexed attempt here is the attempt *just completed*, so
    retry 1 waits `base`, retry 2 waits `base*2`, up to `cap`.  This
    matches spec §7.2 "exponential backoff retry" without requiring a
    separate attempt-scale formula — the same shape is used for both
    outbox retries and WORM retries.
    """
    seconds = settings.outbox_backoff_base_seconds * (2 ** max(0, attempt - 1))
    seconds = min(seconds, settings.outbox_backoff_cap_seconds)
    return timedelta(seconds=seconds)


async def _claim_batch(
    session: AsyncSession,
    *,
    limit: int,
    now: datetime,
    consumer_name: str = PRIMARY_CONSUMER,
) -> list[DomainEventDelivery]:
    """Claim up to `limit` pending-or-failed deliveries ready to retry.

    Uses FOR UPDATE SKIP LOCKED so concurrent workers never contend
    for the same row (spec §2.3).  `available_at <= now` filters out
    rows whose backoff window has not yet elapsed.  Status matches
    `pending` OR `failed`: pending is a fresh delivery, failed is a
    prior attempt that should retry once available_at is reached.
    Dead-letter rows are never picked up.

    Why consumer_name: spec §4.5 models per-consumer deliveries — a
    future fan-out slice will register a second consumer whose
    handler ≠ the primary handler.  Filtering by consumer_name here
    means each worker instance only touches its own rows and never
    runs another consumer's handler against its own deliveries.  4B
    review finding: omit this filter and a multi-consumer fleet
    stomps on each other's queues.
    """
    stmt = (
        select(DomainEventDelivery)
        .where(
            DomainEventDelivery.consumer_name == consumer_name,
            DomainEventDelivery.status.in_(
                (DeliveryStatus.pending, DeliveryStatus.failed),
            ),
            DomainEventDelivery.available_at <= now,
        )
        .order_by(DomainEventDelivery.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _load_event(
    session: AsyncSession, event_id: _uuid.UUID,
) -> DomainEvent | None:
    """Load the DomainEvent row for a delivery."""
    return await session.get(DomainEvent, event_id)


async def _mark_delivered(
    delivery: DomainEventDelivery, *, now: datetime,
) -> None:
    """Transition delivery: pending|failed → delivered."""
    delivery.status = DeliveryStatus.delivered
    delivery.last_attempt_at = now
    delivery.delivered_at = now
    delivery.last_error = None


async def _mark_retry_or_dead(
    delivery: DomainEventDelivery,
    *,
    error: str,
    settings: Settings,
    now: datetime,
) -> None:
    """Transition delivery: pending|failed → failed (retry) or dead_letter.

    State leaving: pending or failed.
    State entering: failed if attempt_count < max_attempts, otherwise
    dead_letter (spec §4.5, §5.13).
    Invariant: attempt_count is incremented exactly once per attempt;
    next_attempt_at + available_at are set together so the next claim
    respects the backoff window.
    """
    delivery.attempt_count += 1
    delivery.last_attempt_at = now
    delivery.last_error = error[:4000]  # table limit

    if delivery.attempt_count >= delivery.max_attempts:
        delivery.status = DeliveryStatus.dead_letter
        delivery.next_attempt_at = None
        return

    backoff = _compute_backoff(delivery.attempt_count, settings)
    delivery.status = DeliveryStatus.failed
    delivery.next_attempt_at = now + backoff
    delivery.available_at = now + backoff


async def _process_delivery(
    delivery: DomainEventDelivery,
    session: AsyncSession,
    settings: Settings,
    now: datetime,
) -> bool:
    """Dispatch one delivery to its handler.

    Returns True on success (delivered), False on retry/dead-letter.
    Any exception raised by the handler is caught here and converted
    to a retry/dead-letter transition — a handler crash must not
    take down the worker or leave a row stuck in processing.
    """
    event = await _load_event(session, delivery.event_id)
    if event is None:
        # Orphan delivery — the event row was deleted.  Mark delivered
        # so the worker does not spin on it forever.
        await _mark_delivered(delivery, now=now)
        return True

    try:
        handler = handler_for(event.event_type)
    except KeyError:
        await _mark_retry_or_dead(
            delivery, error=f"No handler for {event.event_type.value}",
            settings=settings, now=now,
        )
        return False

    # Move to processing so admin views can distinguish "claimed by a
    # live worker" from "waiting for next poll."  The row is already
    # locked FOR UPDATE, so the status update is safe without a
    # separate commit.
    delivery.status = DeliveryStatus.processing

    try:
        await handler(event, session)
    except Exception as exc:
        logger.exception(
            "Handler for event %s failed", event.event_type.value,
        )
        await _mark_retry_or_dead(
            delivery, error=str(exc), settings=settings, now=now,
        )
        return False

    await _mark_delivered(delivery, now=now)
    return True


async def process_once(
    session: AsyncSession, settings: Settings | None = None,
) -> int:
    """Run a single worker tick.  Returns how many deliveries were claimed.

    Exposed so tests can drive the worker deterministically without a
    time-based loop.  Commits the claimed batch as a single
    transaction — either every handler's mutations land with every
    delivery's terminal state, or nothing does.
    """
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    batch = await _claim_batch(session, limit=cfg.outbox_batch_size, now=now)
    for delivery in batch:
        await _process_delivery(delivery, session, cfg, now)
    await session.commit()
    return len(batch)


async def run_forever(settings: Settings | None = None) -> None:
    """Long-running worker loop.

    Sleeps `outbox_poll_interval_seconds` between ticks.  Opens a
    fresh session per iteration so a transient DB blip in one tick
    does not wedge the worker.  Not used in tests — tests call
    process_once directly for determinism.
    """
    cfg = settings or get_settings()
    while True:
        try:
            async for session in get_session():
                await process_once(session, cfg)
                break  # one session per iteration
        except Exception:
            logger.exception("Outbox worker iteration failed")
        await asyncio.sleep(cfg.outbox_poll_interval_seconds)
