#!/usr/bin/env python3
"""
Module:   Slice 5B-10 + 5B-14 Migration
Purpose:  (1) Add work_orders.has_graph_issue column (UX spec v1.5.1
          §5.4.6).  (2) Backfill the seeded engineer role row with the
          new wo:recall permission so live-DB effective permissions
          match ROLE_PERMISSIONS in forge.api.auth after the Recall
          transition lands.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: d68f3c2a10b9
Revises: c4a7d2e91b38
Create Date: 2026-04-14 19:00:00.000000

Why this migration:
  Slice 5B-10 adds `has_graph_issue` to the work_orders table.  The
  column is the canonical field named by UX spec §5.4.6 — it gates
  the [Fix Graph] / [Report Graph Issue] UI buttons and is set by
  server validation paths that land in later graph-write slices.
  `server_default='false'` keeps legacy raw-SQL inserts (several
  test fixtures) working without listing the new column.

  Slice 5B-14 introduces the Recall transition (released → draft,
  UX spec §5.3 state-machine table).  Recall is engineer-only per
  the §5.3 permission matrix, so a new wo:recall permission lands
  on the engineer role.  The DB seed migration (387cadd44f9d) must
  be aligned the same way inspect:read was in c4a7d2e91b38 — live
  effective_permissions reads from DB role rows, not from
  ROLE_PERMISSIONS, so skipping the seed update re-opens the exact
  drift the 5B-7 review caught.

  Both concerns land in one migration because they share the
  "5B-10 + 5B-14 review-cycle" rationale and would otherwise
  require two migrations that depend on each other with no
  independently-meaningful cold-upgrade state.  Precedent: the
  c4a7d2e91b38 migration combined inspect:read seed + active_since
  column under the same slice boundary.

  The jsonb-dedupe permission update uses `cast(permissions AS
  jsonb)` because the column is stored as `json` (not jsonb) per the
  initial schema, and the `-` array-subtract operator only works on
  jsonb — a lesson also recorded in c4a7d2e91b38.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d68f3c2a10b9"
down_revision: str | Sequence[str] | None = "c4a7d2e91b38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Role-to-new-permissions map.  Narrowly scoped to the engineer
# role — UX spec v1.5.1 §5.3 permission matrix names engineer as
# the only role that can recall a released WO.
ROLE_PERMISSION_ADDITIONS: dict[str, list[str]] = {
    "engineer": ["wo:recall"],
}


def upgrade() -> None:
    """Add has_graph_issue column + backfill wo:recall permission."""

    # ------------------------------------------------------------------
    # Part 1: work_orders.has_graph_issue column.
    # ------------------------------------------------------------------
    # nullable=False + server_default="false" so existing rows are
    # backfilled atomically inside the ALTER TABLE (no separate
    # UPDATE pass needed).  The default mirrors the model default
    # exactly so an ORM-driven INSERT and a raw-SQL INSERT produce
    # the same row, matching the pattern established by the program
    # feature-flag migration (02f7eb54dfff).
    op.add_column(
        "work_orders",
        sa.Column(
            "has_graph_issue",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ------------------------------------------------------------------
    # Part 2: engineer role gains wo:recall.
    # ------------------------------------------------------------------
    # The `- :perm || to_jsonb(...)` idiom deduplicates the array
    # first so re-running the migration on a DB that already has
    # the permission (e.g. from a manual hotfix or snapshot restore)
    # stays idempotent.  The outer `::json` keeps the column
    # storage type unchanged — permissions is `json`, not `jsonb`.
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
    """Reverse the two changes in opposite order.

    The role-permission reversal is best-effort idempotent — if an
    operator re-granted wo:recall through the admin API after the
    migration ran, this downgrade strips it back out.  For a clean
    rollback, run the admin-level "grant engineer wo:recall" tool
    again after downgrade.
    """
    # Drop the column first so any constraints-dependent on it are
    # gone before we touch unrelated tables.
    op.drop_column("work_orders", "has_graph_issue")

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
