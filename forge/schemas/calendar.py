#!/usr/bin/env python3
"""
Module:   Calendar Schemas
Purpose:  Pydantic request/response models for work-calendar endpoints
          (spec §5.10 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Calendars are shared master data — no program_id.  CreateCalendar
  carries the full working-day set up front; Update is sparse so a
  single field can change without restating the whole calendar.
  working_days values are weekday ints (0=Monday..6=Sunday) matching
  datetime.weekday() so the CPM engine can consume them without a
  lookup table.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator


def _validate_weekday_ints(value: list[int]) -> list[int]:
    """Enforce working_days ∈ [0, 6] (Monday..Sunday per datetime.weekday()).

    Why here: datetime.weekday() returns 0=Monday..6=Sunday, and the CPM
    engine compares `date.weekday()` against this set directly.  Any
    value outside 0..6 can never match a real weekday, which would
    silently make every day non-working and drop the project into an
    infinite loop in add_working_hours.  Fail loud at the boundary so
    a misconfigured calendar cannot reach the engine.
    """
    for day in value:
        if not 0 <= day <= 6:
            raise ValueError(
                f"working_days values must be in 0..6 (Monday..Sunday); "
                f"got {day}"
            )
    return value


class CalendarCreate(BaseModel):
    """Request body for POST /api/calendars."""

    name: str = Field(max_length=200)
    working_hours_per_day: float = Field(gt=0, le=24)
    working_days: list[int] = Field(
        description="Weekday ints 0=Monday..6=Sunday",
    )
    holidays: list[str] = Field(
        default_factory=list,
        description="ISO date strings YYYY-MM-DD",
    )
    effective_from: date
    effective_to: date | None = None

    @field_validator("working_days")
    @classmethod
    def _check_working_days(cls, value: list[int]) -> list[int]:
        return _validate_weekday_ints(value)


class CalendarUpdate(BaseModel):
    """Request body for PATCH /api/calendars/{id}."""

    version: int
    name: str | None = Field(default=None, max_length=200)
    working_hours_per_day: float | None = Field(default=None, gt=0, le=24)
    working_days: list[int] | None = None
    holidays: list[str] | None = None
    effective_from: date | None = None
    effective_to: date | None = None

    @field_validator("working_days")
    @classmethod
    def _check_working_days(
        cls, value: list[int] | None,
    ) -> list[int] | None:
        """Same 0..6 gate as create — but tolerant of the field being
        omitted on a partial update.  If the caller does not send
        working_days, it stays at its current DB value.
        """
        if value is None:
            return None
        return _validate_weekday_ints(value)


class CalendarOut(BaseModel):
    """WorkCalendar in API responses."""

    id: uuid.UUID
    name: str
    working_hours_per_day: float
    working_days: list[int]
    holidays: list[str]
    effective_from: date
    effective_to: date | None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
