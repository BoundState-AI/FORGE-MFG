#!/usr/bin/env python3
"""
Module:   5B-7 Review Fix Migration
Purpose:  (1) Backfill the seeded `inspector` and `qa_manager` role rows
          with the new `inspect:read` permission so live-DB effective
          permissions match ROLE_PERMISSIONS in forge.api.auth.
          (2) Add `active_since` to operations for per-cycle overdue
          derivation (UX spec v1.5.1 §5.1).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: c4a7d2e91b38
Revises: 02f7eb54dfff
Create Date: 2026-04-14 17:15:00.000000

Why this migration:
  Slice 5B-7 introduced a new `inspect:read` permission on the inspector
  and qa_manager roles (UX spec v1.5.1 §3.1 fn4: qa_manager has
  read-only queue drill-through).  The auth code map was updated in
  forge/api/auth.py but the seed migration (387cadd44f9d) was left
  unchanged, so `/api/users/me` — which reads DB role rows via
  admin_service.effective_permissions — returned stale permission
  sets.  The frontend will key nav/guards off that self-profile, so
  drift here becomes a user-facing "cannot access queue" error even
  though the route itself authorizes correctly.  This migration
  aligns the live DB to the code map.

  The second half adds `operations.active_since` — a per-cycle
  timestamp that resets on rework → in_progress, unlike `started_at`
  which is set once.  Spec §5.1 explicitly names `operation.active_since`
  as the clock source for the overdue threshold; using `started_at`
  (carry-forward from slice 5B-6 → 5B-7) was a documented but real
  spec mismatch because a reworked-and-restarted op would re-enter
  the Inspection Queue already overdue from its original start time.

  Backfill: existing rows get `active_since = started_at` (best we
  can reconstruct without labor-history archaeology).  This is
  historically inaccurate for ops that have been reworked before the
  column existed, but correct for every op going forward.  Documenting
  so that an auditor who finds pre-migration reworked ops with
  still-old overdue clocks understands why.

  Why a single migration rather than two: both fixes land in the same
  review cycle and share the "5B-7 correctness" rationale.  Splitting
  them would require the second migration to depend on the first and
  would make neither independently meaningful on a cold upgrade.

  The permission update uses jsonb array operations so an operator who
  re-seeded the DB from a snapshot (with inspect:read already present)
  does not get a duplicate.  The `-` followed by `||` idiom deduplicates
  by pulling any existing entry out first and then appending fresh.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7d2e91b38"
down_revision: str | Sequence[str] | None = "02f7eb54dfff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Role-to-new-permissions map for this fix.  Narrowly scoped — other
# role drift (if any) belongs in its own migration with its own spec
# traceability in the docstring.
ROLE_PERMISSION_ADDITIONS: dict[str, list[str]] = {
    "inspector": ["inspect:read"],
    "qa_manager": ["inspect:read"],
}


def upgrade() -> None:
    """Align DB role seed with forge.api.auth + add operations.active_since."""

    # ------------------------------------------------------------------
    # Part 1: role permission seed alignment.
    # ------------------------------------------------------------------
    # Drift from slice 5B-7: ROLE_PERMISSIONS in auth.py added
    # inspect:read to inspector + qa_manager, but the DB seed
    # (387cadd44f9d) predates that change.  /api/users/me reads DB
    # role rows for the effective permission set, so the frontend
    # self-profile would return stale perms and hide the Inspection
    # Queue from roles that do have route-level access.  The jsonb
    # array-dedupe pattern (`- 'x' || to_jsonb('x')`) keeps the
    # migration idempotent on DBs that already contain the permission
    # from a snapshot restore or a manual hotfix.
    for role_name, new_perms in ROLE_PERMISSION_ADDITIONS.items():
        for perm in new_perms:
            # The permissions column is stored as `json` (not jsonb) per
            # the initial schema, and the `-` array-subtract operator
            # only works on jsonb — so cast in before subtracting, then
            # cast back to json to keep the column shape unchanged.
            # `cast(:perm AS text)` rather than `:perm::text` because
            # Postgres's `::` cast syntax collides with SQLAlchemy's
            # named-parameter parser and the latter raises a syntax
            # error at prepare time.
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

    # ------------------------------------------------------------------
    # Part 2: operations.active_since column.
    # ------------------------------------------------------------------
    # Nullable because (a) ops in `pending` status legitimately have
    # no active_since, and (b) the backfill strategy (copy started_at)
    # is null-safe — pending rows stay null.  No server_default: we
    # want a missing active_since to be explicit rather than masked
    # by a column default.
    op.add_column(
        "operations",
        sa.Column(
            "active_since",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Backfill: for every op that currently has a started_at, seed
    # active_since with the same value.  This is "first-cycle correct":
    # for ops never reworked, active_since == started_at.  For ops
    # that HAVE been reworked before this migration, the overdue clock
    # will remain biased toward the original start until the next
    # rework → in_progress cycle, at which point the application
    # layer resets active_since to NOW().  The carry-forward window
    # is bounded and documented.
    op.execute(sa.text("""
        UPDATE operations
        SET active_since = started_at
        WHERE started_at IS NOT NULL
    """))


def downgrade() -> None:
    """Reverse the two changes in opposite order.

    The role-permission part is best-effort reversible — we remove
    the added entry from the jsonb array if it is still present.
    If an operator has since re-granted the permission through the
    admin API, this downgrade leaves that intact (we strip all
    matches of the exact string, so a single entry is cleaned up).
    """
    op.drop_column("operations", "active_since")

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
