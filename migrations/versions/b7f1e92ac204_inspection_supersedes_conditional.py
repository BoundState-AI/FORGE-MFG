#!/usr/bin/env python3
"""
Module:   Inspection Supersedes-Conditional Migration
Purpose:  Add inspection_events.supersedes_inspection_id (UX spec
          v1.5.1 §5.5.5 / R5-R2-ISSUE-006) — a nullable self-reference
          letting a Resolve Conditional Finding inspection name the
          prior conditional it resolves.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: b7f1e92ac204
Revises: f4d8b2e6c31a
Create Date: 2026-04-15 10:00:00.000000

Why this migration:
  §5.5.5 Resolve Conditional Finding: when an inspector accepts or
  rejects a prior conditional inspection, the new event records a
  pointer back to the conditional it supersedes so the audit chain
  and queue-derivation logic can tell a resolution event from a
  fresh inspection.

  Nullable: the overwhelming majority of inspections are first-time
  events with no predecessor.  Existing rows are all first-time
  events by definition (the column did not exist), so the column
  lands nullable with no backfill and no default.

  FK `inspection_events.id` with ondelete=RESTRICT: the referent is
  part of the audit chain, so deleting the superseded inspection
  from underneath a superseding one would leave an orphaned pointer.
  RESTRICT matches the append-only posture the rest of the audit
  surface already enforces (46a86e0c1628 trigger).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7f1e92ac204"
down_revision: str | Sequence[str] | None = "f4d8b2e6c31a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable self-ref column to inspection_events."""
    op.add_column(
        "inspection_events",
        sa.Column(
            "supersedes_inspection_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey(
                "inspection_events.id",
                name="fk_inspection_events_supersedes",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the column and its self-referential FK."""
    op.drop_constraint(
        "fk_inspection_events_supersedes",
        "inspection_events",
        type_="foreignkey",
    )
    op.drop_column("inspection_events", "supersedes_inspection_id")
