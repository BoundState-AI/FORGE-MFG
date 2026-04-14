#!/usr/bin/env python3
"""
Module:   Event Handlers
Purpose:  Concrete handlers invoked by the outbox worker for
          §4.5 event types.  Currently: schedule recalculation
          (spec §8.2) and no-op catch-all.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §8.2: "OperationStatusChanged and NCRStatusChanged events
  trigger CPM recalculation via outbox."  That means the handler
  runs in the worker, not the request thread — the request already
  committed its aggregate change before this handler sees it.

  recalculate_schedule is idempotent: it hydrates the CPM input,
  runs compute_cpm, and writes forecast_finish.  Re-runs are safe.
  If no schedule exists for the aggregate (e.g., WO has no schedule
  yet), the handler returns cleanly — a logical no-op, not an error.

  Handlers do NOT call session.commit().  The worker owns the
  transaction boundary — the handler mutation and the delivery-row
  mark-delivered land in the same commit.  That is what makes the
  outbox transactional (spec §2.3).
"""

from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models.event import DomainEvent
from forge.models.schedule import BuildSchedule
from forge.services.cpm_engine import add_working_hours, compute_cpm
from forge.services.schedule_service import (
    _load_calendar,
    _load_links,
    _load_ops,
    _to_cpm_calendar,
    _to_cpm_links,
    _to_cpm_ops,
)

logger = logging.getLogger(__name__)


async def noop(event: DomainEvent, session: AsyncSession) -> None:
    """No-op handler for event types with no registered consumer.

    We still deliver (mark-delivered) so dead-letters only hold
    genuinely failed deliveries, not events waiting for a consumer
    that hasn't been written yet.  A debug log records each no-op
    dispatch for observability.
    """
    del session
    logger.debug(
        "Event %s (%s) dispatched via no-op handler",
        event.event_type.value, event.id,
    )


async def recalculate_schedule(
    event: DomainEvent, session: AsyncSession,
) -> None:
    """Recompute the schedule forecast for an event's aggregate.

    State leaving: schedule.forecast_finish.
    State entering: re-derived schedule.forecast_finish from live CPM.
    Invariants:
      1. Aggregate is a work_order (OperationStatusChanged) or an
         ncr (NCRStatusChanged).  For NCRs we look up the parent WO.
      2. If no schedule exists for the WO, the handler is a no-op —
         a WO that has not been scheduled cannot be recalculated.
      3. If the current CPM result contains a cycle, the forecast is
         left unchanged.  A cycle is a data problem, not a scheduling
         emergency for the worker — surfacing it through an alert is
         the right escalation path (spec §7.1 admin visibility).
    """
    wo_id = await _resolve_work_order_id(event, session)
    if wo_id is None:
        return

    sched = await _schedule_for_wo(session, wo_id)
    if sched is None:
        return

    ops = await _load_ops(session, wo_id)
    links = await _load_links(session, sched.id)
    cal = await _load_calendar(session, sched.calendar_id)

    result = compute_cpm(_to_cpm_ops(ops), _to_cpm_links(links))
    if result.cycle_detected:
        logger.warning(
            "Schedule %s has a dependency cycle; skipping recalc",
            sched.id,
        )
        return

    sched.forecast_finish = add_working_hours(
        sched.baseline_start,
        result.total_duration_hours,
        _to_cpm_calendar(cal),
    )
    await session.flush()


async def _resolve_work_order_id(
    event: DomainEvent, session: AsyncSession,
) -> _uuid.UUID | None:
    """Extract the affected WO id from an event.

    For work_order aggregates, event.aggregate_id is the WO id.
    For ncr aggregates, we look up the NCR's work_order_id.
    Returns None if the aggregate is not one we can route — the
    handler swallows such events quietly rather than dead-lettering.
    """
    if event.aggregate_type == "work_order":
        return _uuid.UUID(event.aggregate_id)
    if event.aggregate_type == "ncr":
        from forge.models.ncr import NonconformanceReport

        ncr = await session.get(
            NonconformanceReport, _uuid.UUID(event.aggregate_id),
        )
        return ncr.work_order_id if ncr is not None else None
    return None


async def _schedule_for_wo(
    session: AsyncSession, wo_id: _uuid.UUID,
) -> BuildSchedule | None:
    """Load the BuildSchedule tied to this WO, or None."""
    result = await session.execute(
        select(BuildSchedule).where(BuildSchedule.work_order_id == wo_id)
    )
    return result.scalar_one_or_none()
