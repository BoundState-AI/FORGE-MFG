#!/usr/bin/env python3
"""
Module:   GC-3 — users.last_auth_at fallback freshness column
Purpose:  Add ``users.last_auth_at`` so the auth-freshness helper has a
          DB-backed source for the protected-action freshness gate when
          Cognito does not surface the JWT ``auth_time`` claim.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: f1a2b3c4d5e6
Revises: e12f7a5b90cd
Create Date: 2026-04-16 09:00:00.000000

Why this migration:
  UX spec v1.5.1 §9.1 names the JWT ``auth_time`` claim as the primary
  source for re-auth freshness checks on safety-critical actions.  The
  spec also documents an explicit fallback: "Fallback if that
  verification fails: server reads ``last_auth_at`` from the application
  database instead of JWT ``auth_time``."  Build Spec §9.3.6 line 1287
  echoes the same fallback.

  This migration ships the column so the fallback path is real before
  any production incident pressures it.  ``Settings.auth_freshness_source``
  selects which path is live; the column stays nullable because most
  existing rows have no authentication history at migration time and the
  column is only consulted when the fallback is enabled.

  Backfill: none.  Rows that pre-date this migration get NULL, which the
  freshness helper treats as "no auth history → must re-authenticate"
  on the DB-fallback path.  That posture is intentionally conservative
  for first-time users in a fresh environment.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e12f7a5b90cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable last_auth_at column to users."""
    op.add_column(
        "users",
        sa.Column(
            "last_auth_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Last successful authentication timestamp; freshness "
                "fallback source per UX spec v1.5.1 §9.1 when the JWT "
                "auth_time claim is unavailable."
            ),
        ),
    )


def downgrade() -> None:
    """Drop the column.  Idempotent on rollback."""
    op.drop_column("users", "last_auth_at")
