#!/usr/bin/env python3
"""
Module:   Event Types + Handler Registry
Purpose:  Canonical mapping from spec §4.5 EventType values to the
          handler callable that processes each one.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Spec §4.5 enumerates the 17 canonical event types.  The worker needs
  to know "for event X, which handler to invoke"; the admin UI
  expects "every event type has a registered consumer, or it will
  dead-letter immediately."  Keeping the registry here (rather than
  implicitly scattered across handler modules) means a single file is
  the authoritative source of coverage.

  tests/unit/test_event_types.py locks the EventType enum to this
  registry — every enum value must have a handler, no extras.  If an
  event type is added to the spec, adding it to the enum without
  registering here will break the test immediately.

  Handlers are async callables taking (DomainEvent, AsyncSession).
  The worker passes its session so handlers can read/write domain
  data inside the same transaction that clears the delivery row —
  partial progress is impossible (spec §2.3 transactional outbox).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from forge.models.enums import EventType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from forge.models.event import DomainEvent

# Public alias — every handler in the registry must conform.
EventHandler = Callable[
    ["DomainEvent", "AsyncSession"], Awaitable[None],
]

# Sentinel consumer name used by bus.emit_event + the worker.  Phase 1
# runs a single logical consumer per event; multi-consumer fan-out is
# an explicit future extension (spec §4.5 only defines the delivery
# row schema, not a fan-out model).
PRIMARY_CONSUMER = "primary"


def _load_handlers() -> dict[EventType, EventHandler]:
    """Build the EventType → handler registry.

    Imported lazily to avoid a circular import: handlers.py imports
    from services, which import from models, which import from here
    indirectly.  Performing the registration inside a function breaks
    that cycle without giving up static coverage — the registry is
    still fully populated at first access.
    """
    from forge.events import handlers

    return {
        # Schedule-relevant events — §8.2 event-driven recalculation
        EventType.operation_status_changed: handlers.recalculate_schedule,
        EventType.ncr_status_changed: handlers.recalculate_schedule,
        # Remaining §4.5 vocabulary — no-op handlers so the vocabulary
        # test passes and future consumers land in one place.  These
        # are not silently dropped: the worker still marks the
        # delivery delivered, which is the correct outcome when no
        # consumer has opted in yet.
        EventType.work_order_created: handlers.noop,
        EventType.work_order_released: handlers.noop,
        EventType.work_order_status_changed: handlers.noop,
        EventType.operation_started: handlers.noop,
        EventType.operation_completed: handlers.noop,
        EventType.operation_reworked: handlers.noop,
        EventType.inspection_recorded: handlers.noop,
        EventType.sign_off_recorded: handlers.noop,
        EventType.ncr_created: handlers.noop,
        EventType.ncr_dispositioned: handlers.noop,
        EventType.material_received: handlers.noop,
        EventType.material_accepted: handlers.noop,
        EventType.material_consumed: handlers.noop,
        EventType.document_finalized: handlers.noop,
        EventType.chat_queried: handlers.noop,
    }


_registry: dict[EventType, EventHandler] | None = None


def get_registry() -> dict[EventType, EventHandler]:
    """Return the (lazily-initialized) EventType → handler registry.

    Memoized so the import cycle only runs once per process.
    """
    global _registry
    if _registry is None:
        _registry = _load_handlers()
    return _registry


def handler_for(event_type: EventType) -> EventHandler:
    """Look up a handler by event type, or raise KeyError.

    KeyError here would mean an event is emitted with a type that has
    no registered consumer — that is a coding error, not a runtime
    failure, and the vocabulary test should have caught it before the
    change merged.
    """
    return get_registry()[event_type]
