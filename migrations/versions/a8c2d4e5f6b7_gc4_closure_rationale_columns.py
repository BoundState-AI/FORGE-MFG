#!/usr/bin/env python3
"""
Module:   GC-4 — closure_rationale columns on work_orders + nonconformance_reports
Purpose:  Add the optional `closure_rationale` field that the Phase-1
          closure-as-attestation contract persists on the closed entity.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: a8c2d4e5f6b7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-16 16:00:00.000000

Why this migration:
  UX spec v1.5.1 §5.3 (Closure-as-Attestation) and Build spec
  §5.1 / §5.5 both name `closure_rationale` as part of the WO and
  NCR close request contracts.  The field is optional — Phase-1
  attestation is the click-to-close action itself; rationale is a
  free-form QA note recorded when the closer wants to add context.
  Persisting it on the closed entity means the QA gate's
  reasoning is reviewable from the entity row directly, not just
  from the audit chain.

Backfill:
  None.  Existing closed rows (if any) get NULL — they pre-date
  the contract and we don't fabricate a rationale we don't have.
  The frontend treats NULL as "no rationale recorded."

Rollback:
  Idempotent column drop.  Loses any persisted rationale text;
  the audit chain still carries it in `after_state` so the
  attestation history survives.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8c2d4e5f6b7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable `closure_rationale` to work_orders + NCRs."""
    op.add_column(
        "work_orders",
        sa.Column(
            "closure_rationale",
            sa.String(length=4000),
            nullable=True,
            comment=(
                "Phase-1 closure-as-attestation note (UX spec §5.3). "
                "Free-form QA rationale captured at close time; "
                "NULL when the closer chose not to record one."
            ),
        ),
    )
    op.add_column(
        "nonconformance_reports",
        sa.Column(
            "closure_rationale",
            sa.String(length=4000),
            nullable=True,
            comment=(
                "Phase-1 closure-as-attestation note (UX spec §5.6.7). "
                "Distinct from `root_cause` — this is the QA closer's "
                "judgement on why this NCR is being closed now."
            ),
        ),
    )


def downgrade() -> None:
    """Drop both columns.  Idempotent on rollback."""
    op.drop_column("nonconformance_reports", "closure_rationale")
    op.drop_column("work_orders", "closure_rationale")
