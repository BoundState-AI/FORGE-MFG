#!/usr/bin/env python3
"""
Module:   Schedule Model
Purpose:  BuildSchedule, ScheduleLink, Milestone, WorkCalendar [CONDITIONAL].

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  Scheduling entities are defined in the schema even when the
  feature_cpm_scheduling flag is off — DomainEvent rows may reference
  schedule aggregates before the CPM worker exists (CLAUDE.md stage
  gate 4 note).  ScheduleLink includes cycle detection responsibility
  in the service layer, not the model.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditableMixin, Base
from .enums import LinkType, MilestoneStatus, ScheduleStatus


class WorkCalendar(AuditableMixin, Base):
    """Working schedule definition with holidays and hours per day (spec §3.1).

    Defines which days are working days, hours per day, and holiday
    exceptions.  Used by the CPM engine to convert lag_hours on
    ScheduleLinks into calendar durations.
    """

    __tablename__ = "work_calendars"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    working_hours_per_day: Mapped[float] = mapped_column(Float, nullable=False)
    working_days: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, default=list
    )
    holidays: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class BuildSchedule(AuditableMixin, Base):
    """CPM schedule tied 1:1 to a work order (spec §3.1, §8.1).

    forecast_finish is auto-calculated by the CPM engine.  Status
    transitions: draft → baselined → in_progress → complete.
    """

    __tablename__ = "build_schedules"

    work_order_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"),
        unique=True, nullable=False,
    )
    calendar_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_calendars.id"), nullable=False
    )
    baseline_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    baseline_finish: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Auto-calculated by the CPM engine on each recalculation pass
    forecast_finish: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[ScheduleStatus] = mapped_column(
        default=ScheduleStatus.draft, nullable=False
    )


class ScheduleLink(AuditableMixin, Base):
    """Dependency link between two operations within a schedule (spec §3.1).

    Cycle detection on every link creation/update is enforced at the
    service layer — circular dependencies are rejected with 422.
    Links are modifiable only when the schedule is in draft status.
    """

    __tablename__ = "schedule_links"

    schedule_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("build_schedules.id"), nullable=False
    )
    predecessor_op_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    successor_op_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False
    )
    link_type: Mapped[LinkType] = mapped_column(
        default=LinkType.finish_to_start, nullable=False
    )
    lag_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class Milestone(AuditableMixin, Base):
    """Schedule milestone with computed status tracking (spec §3.1).

    Status (on_track, at_risk, behind, complete) is computed by the
    CPM engine based on forecast_finish vs target_date.
    """

    __tablename__ = "milestones"

    schedule_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("build_schedules.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[MilestoneStatus] = mapped_column(
        default=MilestoneStatus.on_track, nullable=False
    )
    program_milestone_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
