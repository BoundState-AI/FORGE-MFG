#!/usr/bin/env python3
"""
Module:   Slice 5B-11 Migration
Purpose:  Add `operation_predecessors` association table for explicit
          operation predecessor edges (UX spec v1.5.1 §5.4.6).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: ea5c8f217b40
Revises: d68f3c2a10b9
Create Date: 2026-04-14 19:40:00.000000

Why this migration:
  Two concerns ship together under the 5B-11 boundary:

    (1) UX spec §5.4.6 explicit predecessor edges — new association
        table `operation_predecessors`.
    (2) New `wo:graph_edit` permission on the engineer role, seeded
        into the DB so live effective_permissions match
        ROLE_PERMISSIONS (same drift posture as c4a7d2e91b38 +
        d68f3c2a10b9).

  (1) — predecessor edges.  The default-sequential semantics are a
  fallback for authoring convenience, not the durable source of
  truth.  Prior slices (5B-6) loaded "predecessors" via sort-
  siblings, which is sequence-correlated and cannot represent
  branching graphs, fan-in / fan-out, or any non-linear execution
  dependency.  Fix Graph (PATCH /api/work-orders/{id}/graph) and
  Report Graph Issue (POST /graph/validate) both operate on this
  edge set.

  (2) — graph edit permission.  UX spec §5.3 + §5.4.6 name the
  Fix Graph / Report Graph Issue endpoints as engineer-only on
  held WOs.  A dedicated `wo:graph_edit` permission rather than
  reusing `wo:update` keeps graph mutation distinct from the draft-
  only metadata PATCH surface — if a future slice widens
  `wo:update`, graph editing does not silently widen along with it.

  Schema choice: composite-PK association table rather than an
  ARRAY column on `operations`.  Rationale:

    - FK integrity to operations.id for both endpoints — invalid
      edges cannot exist at all, closing the "dangling predecessor"
      failure mode at the storage layer (the validator still reports
      it as WO-REL-103 when the validator runs on an in-memory
      working copy during Fix Graph).
    - Composite PK (operation_id, predecessor_id) prevents duplicate
      edges without additional application logic.
    - CHECK (operation_id != predecessor_id) blocks self-reference
      at the DB — defense in depth so the validator is not the
      only gate.
    - Cross-WO edges (predecessor points at an op in a different
      WO) are NOT enforced at the DB because FK can't express
      "operations share work_order_id"; the app validator is the
      enforcement point.  Documenting here so a future DBA does
      not add a trigger assuming the constraint is already DB-side.

  ON DELETE CASCADE on both FK columns keeps the edge table
  self-consistent when an operation is deleted (the draft-WO op
  delete path).  There is no separate cleanup sweep.

  No data to backfill: existing operations inherit default-sequential
  semantics as a fallback read-path behavior.  Callers who want
  explicit edges create them via the Fix Graph endpoint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ea5c8f217b40"
down_revision: str | Sequence[str] | None = "d68f3c2a10b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLE_PERMISSION_ADDITIONS: dict[str, list[str]] = {
    "engineer": ["wo:graph_edit"],
}


def upgrade() -> None:
    """Create the operation_predecessors edge table + seed wo:graph_edit."""
    # Part 1 — edge table.
    op.create_table(
        "operation_predecessors",
        sa.Column(
            "operation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operations.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "predecessor_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operations.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation_id <> predecessor_id",
            name="ck_operation_predecessors_no_self_ref",
        ),
    )
    # Separate index on predecessor_id for reverse-lookup queries
    # ("what succeeds op X?") without scanning the whole edge table.
    # The composite PK already covers operation_id as leading key.
    op.create_index(
        "ix_operation_predecessors_predecessor_id",
        "operation_predecessors",
        ["predecessor_id"],
    )

    # Part 2 — role seed alignment (see module docstring).  Uses the
    # same jsonb-dedupe idiom as c4a7d2e91b38 / d68f3c2a10b9 so this
    # migration stays idempotent on DBs that already have the
    # permission from a snapshot restore.
    for role_name, new_perms in ROLE_PERMISSION_ADDITIONS.items():
        for perm in new_perms:
            op.execute(
                sa.text("""
                    UPDATE roles
                    SET permissions = (
                        (cast(permissions AS jsonb) - :perm)
                        || to_jsonb(cast(:perm AS text))
                    )::json
                    WHERE name = :role
                """).bindparams(role=role_name, perm=perm)
            )


def downgrade() -> None:
    """Drop the edge table + strip wo:graph_edit from role seed."""
    op.drop_index(
        "ix_operation_predecessors_predecessor_id",
        table_name="operation_predecessors",
    )
    op.drop_table("operation_predecessors")
    for role_name, new_perms in ROLE_PERMISSION_ADDITIONS.items():
        for perm in new_perms:
            op.execute(
                sa.text("""
                    UPDATE roles
                    SET permissions = (
                        cast(permissions AS jsonb) - :perm
                    )::json
                    WHERE name = :role
                """).bindparams(role=role_name, perm=perm)
            )
