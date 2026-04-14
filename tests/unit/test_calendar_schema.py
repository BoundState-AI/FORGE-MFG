#!/usr/bin/env python3
"""
Module:   Test Calendar Schema Validation
Purpose:  Unit tests for the working_days 0..6 gate on CalendarCreate /
          CalendarUpdate (spec §5.10 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  datetime.weekday() is defined 0..6 (Monday..Sunday).  The CPM engine
  compares `date.weekday()` against working_days directly; values
  outside 0..6 can never match any real day, so a misconfigured
  calendar would silently make every day non-working and drop the
  project into an infinite loop inside add_working_hours.  These
  tests prove the schema boundary stops that class of bug before it
  reaches the engine.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from forge.schemas.calendar import CalendarCreate, CalendarUpdate


def test_calendar_create_accepts_valid_weekdays() -> None:
    model = CalendarCreate(
        name="Std 5x8",
        working_hours_per_day=8.0,
        working_days=[0, 1, 2, 3, 4],
        effective_from=date(2026, 1, 1),
    )
    assert model.working_days == [0, 1, 2, 3, 4]


def test_calendar_create_accepts_full_week() -> None:
    model = CalendarCreate(
        name="7x8",
        working_hours_per_day=8.0,
        working_days=[0, 1, 2, 3, 4, 5, 6],
        effective_from=date(2026, 1, 1),
    )
    assert model.working_days == [0, 1, 2, 3, 4, 5, 6]


def test_calendar_create_rejects_negative_weekday() -> None:
    with pytest.raises(ValidationError):
        CalendarCreate(
            name="bad",
            working_hours_per_day=8.0,
            working_days=[-1, 0, 1],
            effective_from=date(2026, 1, 1),
        )


def test_calendar_create_rejects_seven() -> None:
    with pytest.raises(ValidationError):
        CalendarCreate(
            name="bad",
            working_hours_per_day=8.0,
            working_days=[0, 1, 7],
            effective_from=date(2026, 1, 1),
        )


def test_calendar_update_without_working_days_is_valid() -> None:
    """Partial update: omitting working_days leaves it unchanged."""
    model = CalendarUpdate(version=1, name="Renamed")
    assert model.working_days is None


def test_calendar_update_with_valid_working_days() -> None:
    model = CalendarUpdate(version=1, working_days=[0, 1, 2])
    assert model.working_days == [0, 1, 2]


def test_calendar_update_rejects_out_of_range_weekday() -> None:
    with pytest.raises(ValidationError):
        CalendarUpdate(version=1, working_days=[0, 8])


def test_calendar_update_rejects_negative_weekday() -> None:
    with pytest.raises(ValidationError):
        CalendarUpdate(version=1, working_days=[-2, 3])
