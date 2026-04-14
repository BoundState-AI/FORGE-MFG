#!/usr/bin/env python3
"""
Module:   Program Feature Flags Migration
Purpose:  Add schedule_enabled, oracle_access, and overdue_threshold_hours
          columns to the programs table to back the X-Program-Id wire
          contract (UX spec v1.4.2 §6.7) and the dashboard overdue
          calculation (UX spec v1.4.2 §5.3).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: 02f7eb54dfff
Revises: a3b83e24cdf1
Create Date: 2026-04-14 12:00:00.000000

Why this migration:
  The frontend UX spec v1.4.2 introduces three per-program switches that
  the existing programs table cannot express:

  - schedule_enabled (default true): gates the schedule UI and the
    /api/schedules/* feature-gated routes.
  - oracle_access (default false): gates the ORACLE Chat surface and
    /api/chat/*.  Default-off because chat is a CONDITIONAL slice and
    must be opted into per program by an admin.
  - overdue_threshold_hours (default 24): drives the dashboard overdue
    inspection rule and operation.is_overdue.  Stored as integer
    calendar hours per spec §5.3.

  All three columns carry server_default values so existing rows are
  backfilled atomically inside the ALTER TABLE — no separate UPDATE
  needed.  The defaults mirror the model defaults exactly so an ORM-
  driven INSERT and a raw-SQL INSERT produce the same row.

  Why server_default (not a bare default): the existing test harness
  inserts programs with raw SQL (`INSERT INTO programs (id, code,
  name, active, created_by, updated_by) VALUES (...)`) and would fail
  the NOT NULL constraint without a database-side default.  The
  server_default ensures backwards compatibility for those inserts and
  for any operator who creates a program directly via psql.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "02f7eb54dfff"
down_revision: str | Sequence[str] | None = "a3b83e24cdf1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the three feature-flag columns to programs with backfill defaults."""
    # schedule_enabled — default true so existing programs keep their
    # schedule surface visible until an admin disables it.
    op.add_column(
        "programs",
        sa.Column(
            "schedule_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # oracle_access — default false because chat is CONDITIONAL.
    # An admin must opt programs into chat explicitly.
    op.add_column(
        "programs",
        sa.Column(
            "oracle_access",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # overdue_threshold_hours — default 24 per UX §5.3.  Integer hours
    # are sufficient: shift-based manufacturing has no business need
    # for sub-hour overdue precision and the admin UI is simpler.
    op.add_column(
        "programs",
        sa.Column(
            "overdue_threshold_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("24"),
        ),
    )


def downgrade() -> None:
    """Drop the three feature-flag columns.  Reversible — no data preserved."""
    op.drop_column("programs", "overdue_threshold_hours")
    op.drop_column("programs", "oracle_access")
    op.drop_column("programs", "schedule_enabled")
