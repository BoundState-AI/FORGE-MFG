#!/usr/bin/env python3
"""
Module:   Program Model
Purpose:  Program entity — top-level access-scoping boundary plus per-program
          feature flags consumed by the X-Program-Id wire contract (UX spec
          v1.4.2 §6.7) and the dashboard overdue calculation (§5.3).

BoundState AI 2026
Version: 0.2.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Programs scope all data access (build spec §5.0.3).  A user's
  custom:programs JWT claim determines which programs' data they can
  see.  Program filtering is enforced at the service layer, not routes.

  Three feature-flag columns live on Program rather than a separate
  ProgramConfig table because (a) cardinality is one row per program,
  (b) the flags are read on every request that reaches the X-Program-Id
  middleware, and (c) the spec treats them as intrinsic program
  attributes — there is no temporal history to track separately.

  - schedule_enabled gates the entire schedule surface (UX §5.7) and
    the schedule navigation item (UX §1.4).  Default true so existing
    programs continue to expose schedules until an admin opts out.
  - oracle_access gates ORACLE Chat (UX §5.9, build spec descope tier 1).
    Default false because chat is a CONDITIONAL slice — an admin must
    explicitly enable it per program before it appears in nav.
  - overdue_threshold_hours drives the dashboard overdue logic
    (UX §5.3).  Default 24 calendar hours per spec.  Stored as integer
    hours — sub-hour granularity is not a product requirement and
    keeps the admin UI a single number-input field.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditableMixin, Base


class Program(AuditableMixin, Base):
    """Program entity — e.g. 'RS25', 'SLS-ADAPTER'."""

    __tablename__ = "programs"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Feature flags — UX spec v1.4.2 §1.4, §5.3, §5.7, §5.9, §6.7.
    # All three carry server_default so legacy raw-SQL INSERTs in the
    # test harness continue to work without listing the new columns.
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False,
    )
    oracle_access: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False,
    )
    overdue_threshold_hours: Mapped[int] = mapped_column(
        Integer, default=24, server_default="24", nullable=False,
    )
