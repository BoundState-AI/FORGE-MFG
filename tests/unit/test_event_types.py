#!/usr/bin/env python3
"""
Module:   Test Event Types
Purpose:  Lock the §4.5 EventType vocabulary to the handler registry
          (CLAUDE.md required verification).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  CLAUDE.md "Required Verification" calls for
  tests/unit/test_event_types.py that "locks the event vocabulary to
  the handler registry."  If the EventType enum and the handler
  registry drift — a new event added without a handler, or a handler
  for a removed type — the outbox will either dead-letter silently or
  call into a ghost consumer.  These tests make that drift a pull-
  request-time failure.
"""

from __future__ import annotations

from forge.events.types import PRIMARY_CONSUMER, get_registry
from forge.models.enums import EventType


def test_registry_covers_every_event_type() -> None:
    """Every EventType value must have a handler registered."""
    registered = set(get_registry().keys())
    enum_values = set(EventType)
    missing = enum_values - registered
    assert not missing, (
        f"EventType(s) with no handler: {sorted(m.value for m in missing)}"
    )


def test_registry_has_no_orphan_handlers() -> None:
    """No registered handler should map to a type outside the enum.

    Prevents stale registrations from surviving an enum rename.
    """
    registered = set(get_registry().keys())
    enum_values = set(EventType)
    extras = registered - enum_values
    assert not extras, (
        f"Handlers registered for unknown EventType(s): {sorted(extras)}"
    )


def test_registry_has_17_entries_matching_spec() -> None:
    """Spec §4.5 defines exactly 17 event types."""
    assert len(get_registry()) == 17
    assert len(EventType) == 17


def test_primary_consumer_name_is_stable() -> None:
    """The bus + worker both key on this value; any change must be deliberate."""
    assert PRIMARY_CONSUMER == "primary"


def test_schedule_relevant_types_route_to_recalc_handler() -> None:
    """Spec §8.2: OperationStatusChanged and NCRStatusChanged trigger recalc."""
    from forge.events import handlers

    registry = get_registry()
    assert (
        registry[EventType.operation_status_changed]
        is handlers.recalculate_schedule
    )
    assert (
        registry[EventType.ncr_status_changed]
        is handlers.recalculate_schedule
    )


def test_non_schedule_types_route_to_noop_handler() -> None:
    """Every other event type lands on the no-op so vocabulary is total."""
    from forge.events import handlers

    registry = get_registry()
    schedule_types = {
        EventType.operation_status_changed,
        EventType.ncr_status_changed,
    }
    for event_type, handler in registry.items():
        if event_type in schedule_types:
            continue
        assert handler is handlers.noop, (
            f"{event_type.value} unexpectedly routes to {handler.__name__}"
        )
