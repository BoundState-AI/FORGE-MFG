#!/usr/bin/env python3
"""
Module:   5C Parity Patch — mat:read seed alignment
Purpose:  Align the seeded `roles` table with the new `mat:read`
          permission added to forge.api.auth.ROLE_PERMISSIONS so the
          DB-backed effective-permissions read on `/api/users/me`
          stays consistent with the in-memory authorization map.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: e12f7a5b90cd
Revises: d8f91a43c5e2
Create Date: 2026-04-15 23:10:00.000000

Why this migration:
  The 5C parity patch adds `GET /api/material-certs` behind a new
  `mat:read` permission.  ROLE_PERMISSIONS in forge/api/auth.py gained
  the string on every role that already had part:read (mirror the
  universal-read posture in UX spec v1.5.1 §3.1 Materials row: receive /
  accept / consume roles read it, plus monitoring roles read it).

  admin_service.effective_permissions reads from the DB `roles` table,
  which `/api/users/me` in turn surfaces to the frontend for nav /
  guards.  Without this migration, the DB seed would drift from
  ROLE_PERMISSIONS and inspector / qa_manager would see a broken
  Materials nav link — the same failure class 5B-7 documented in
  c4a7d2e91b38_inspect_read_seed_and_op_active_since.py.

  The jsonb array-dedupe pattern (`- 'x' || to_jsonb('x')`) keeps the
  migration idempotent on DBs that already contain the permission from
  a snapshot restore or a manual hotfix.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e12f7a5b90cd"
down_revision: str | Sequence[str] | None = "d8f91a43c5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Every existing role gains mat:read.  No role loses a permission and
# no new role is created, so this is purely additive.
ROLE_PERMISSION_ADDITIONS: dict[str, list[str]] = {
    "operator": ["mat:read"],
    "inspector": ["mat:read"],
    "engineer": ["mat:read"],
    "qa_manager": ["mat:read"],
    "program_manager": ["mat:read"],
    "admin": ["mat:read"],
}


def upgrade() -> None:
    """Add mat:read to every seeded role row."""
    for role_name, new_perms in ROLE_PERMISSION_ADDITIONS.items():
        for perm in new_perms:
            # Same jsonb dedupe idiom used in c4a7d2e91b38.  The
            # permissions column is stored as `json`, not `jsonb`, so
            # we cast in for the array-subtract operator and cast back.
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
    """Remove mat:read from every seeded role row.

    Idempotent: stripping the exact string from the jsonb array is a
    no-op when the entry is already absent.  If an operator has
    manually re-granted mat:read through the admin API, this
    downgrade cleans it up as well — the two sources are always
    in sync by design.
    """
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
