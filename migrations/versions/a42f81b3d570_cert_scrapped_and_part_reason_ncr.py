#!/usr/bin/env python3
"""
Module:   Part Scrap Linkage + Cert Scrapped Vocabulary Migration
Purpose:  UX spec v1.5.1 §5.6.5 Part Scrap Transition support:
          (1) add `scrapped` to the cert_status enum (§5.7 filter
          vocabulary), (2) add part_instances.reason_ncr_id for
          the NCR-linked scrap path, and (3) seed the new
          part:scrap permission on the qa_manager and admin roles
          so live DB effective_permissions match auth.py.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: a42f81b3d570
Revises: b7f1e92ac204
Create Date: 2026-04-15 18:00:00.000000

Why this migration:
  §5.6.5 names {status: "scrapped", reason_ncr_id: <ncr_id>} as the
  canonical PATCH payload for the QA-gated part scrap flow.  That
  shape needs three simultaneous DB changes:

    - cert_status gains `scrapped` so the §5.7 materials filter and
      any future material scrap path share a single enum vocabulary.
    - part_instances gains a nullable reason_ncr_id FK so the NCR
      that justified the disposition is persisted.  Nullable keeps
      legacy / non-scrap rows clean with no fake backfill.
    - roles.permissions gains part:scrap on qa_manager and admin so
      live DB effective_permissions align with ROLE_PERMISSIONS in
      auth.py (the precedent established by c4a7d2e91b38 and
      d68f3c2a10b9 — live checks read from DB role rows, not code).

  Landing the three together keeps the upgrade atomic: the enum
  value, the column, and the authority seed are all needed before
  the new PATCH payload is accepted end-to-end.

  FK ondelete=RESTRICT preserves the audit chain — deleting an NCR
  that justified a scrap would leave an orphaned pointer and break
  the §5.6.5 "part SN-XXXX scrapped — ready to close" traceability.

  Cert-enum expansion uses ALTER TYPE ... ADD VALUE IF NOT EXISTS so
  re-running the migration on a hotfix-restored DB is idempotent.
  The ADD VALUE statement cannot run inside a transaction block for
  older Postgres versions, but Alembic's transactional DDL handles
  it on every Postgres version we ship against (14+).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a42f81b3d570"
down_revision: str | Sequence[str] | None = "b7f1e92ac204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Role-to-new-permissions map.  Scoped narrowly to the roles UX spec
# v1.5.1 §5.6.5 names (qa_manager) and the break-glass admin lane.
#
# Why part:update alongside part:scrap: the PATCH /api/parts/:id
# route requires part:update at the FastAPI dependency layer before
# the service's part:scrap check runs.  qa_manager / admin need both
# permissions end-to-end or the scrap flow returns 403 before it
# reaches the §5.6.5 scrap-specific logic.  Seeding part:update here
# also covers the spec-legitimate QA-closure path of editing a
# part's location or parent assembly during closure review.
ROLE_PERMISSION_ADDITIONS: dict[str, list[str]] = {
    "qa_manager": ["part:update", "part:scrap"],
    "admin": ["part:update", "part:scrap"],
}


def upgrade() -> None:
    """Add cert enum value, reason_ncr_id column, and role permissions."""

    # ------------------------------------------------------------------
    # Part 1: cert_status enum gains `scrapped`.
    # ------------------------------------------------------------------
    # IF NOT EXISTS keeps the statement idempotent across snapshot
    # restores.  The enum name is the psql default
    # ("<tablename>_<columnname>_enum"); alembic's auto-generated enums
    # for the initial schema use the shorter `certstatus`.
    op.execute("ALTER TYPE certstatus ADD VALUE IF NOT EXISTS 'scrapped'")

    # ------------------------------------------------------------------
    # Part 2: part_instances.reason_ncr_id column.
    # ------------------------------------------------------------------
    op.add_column(
        "part_instances",
        sa.Column(
            "reason_ncr_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey(
                "nonconformance_reports.id",
                name="fk_part_instances_reason_ncr",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # Part 3: seed part:scrap onto qa_manager + admin role rows.
    # ------------------------------------------------------------------
    # Same jsonb-dedupe idiom as d68f3c2a10b9 — the column is stored as
    # json, and the `-` array-subtract operator only works on jsonb, so
    # we cast both ways to keep the storage type unchanged.
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
    """Reverse the three changes in opposite order.

    Enum value removal is not supported by Postgres without a full
    type rebuild, so the downgrade here leaves the `scrapped` enum
    value in place and only reverses the column + role-permission
    seed — re-upgrading after downgrade is idempotent either way.
    """
    # Reverse role permissions first so we are not holding the role
    # seed in an inconsistent state while DDL runs.
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

    op.drop_constraint(
        "fk_part_instances_reason_ncr",
        "part_instances",
        type_="foreignkey",
    )
    op.drop_column("part_instances", "reason_ncr_id")
