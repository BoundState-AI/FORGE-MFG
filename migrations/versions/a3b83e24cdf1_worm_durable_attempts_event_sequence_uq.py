#!/usr/bin/env python3
"""
Module:   WORM Durable Attempts + Event Sequence Uniqueness
Purpose:  Add worm_replication_attempts sidecar table and a uniqueness
          constraint on DomainEvent (aggregate_type, aggregate_id,
          sequence_number) — 4B review findings.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: a3b83e24cdf1
Revises: 4feca1f5877f
Create Date: 2026-04-14 04:00:00.000000

Why this migration:
  4B review finding (1): the WORM replicator tracked retry attempts in
  process memory — counts were lost on restart, leaving spec §7.2's
  "max 10 attempts" ceiling unenforceable across deployments.  The
  sidecar table persists per-audit attempt state (count, next retry,
  last error, last attempt) without mutating AuditRecord, keeping the
  append-only invariant intact (spec §3.1).  audit_record_id is PK so
  the row-per-audit upsert is a single INSERT ... ON CONFLICT.

  4B review finding (2): emit_event's count+1 sequence_number racedunder concurrent emits on the same aggregate.  The advisory-lock
  fix in bus.py needs a backstop — a unique constraint on
  (aggregate_type, aggregate_id, sequence_number) guarantees that
  even if two writers slip past the lock (future code change,
  misconfiguration, etc.) the DB refuses the duplicate instead of
  silently forking per-aggregate ordering.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b83e24cdf1"
down_revision: str | Sequence[str] | None = "4feca1f5877f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the sidecar table and add the unique constraint."""
    op.create_table(
        "worm_replication_attempts",
        sa.Column(
            "audit_record_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_records.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "attempt_count", sa.Integer, nullable=False,
            server_default="0",
        ),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "last_attempt_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column("last_error", sa.String(length=4000), nullable=True),
    )

    # Unique backstop for emit_event's per-aggregate ordering.
    # Primary enforcement is the advisory lock in bus.py; this is
    # belt-and-suspenders — a concurrent emit that somehow slips the
    # lock fails at INSERT rather than silently duplicating.
    op.create_unique_constraint(
        "uq_domain_events_aggregate_sequence",
        "domain_events",
        ["aggregate_type", "aggregate_id", "sequence_number"],
    )


def downgrade() -> None:
    """Reverse the migration."""
    op.drop_constraint(
        "uq_domain_events_aggregate_sequence",
        "domain_events",
        type_="unique",
    )
    op.drop_table("worm_replication_attempts")
