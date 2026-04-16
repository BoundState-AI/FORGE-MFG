#!/usr/bin/env python3
"""
Module:   GC-2 Reopen / Reactivate Migration
Purpose:  Build spec v1.4.2 GC-2 schema — LaborEntry.execution_cycle
          and NCR.remediation_work_order_id.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: d8f91a43c5e2
Revises: c0d5e871a4b2
Create Date: 2026-04-16 10:00:00.000000

Why this migration:
  GC-2 closes the decision around post-completion rework: the live
  reopen path is a direct complete → in_progress transition, and
  new labor rows must be scoped to the operation's current
  execution_cycle so remediation effort is distinguishable from
  the original-cycle work.

  Two columns land together:

    - labor_entries.execution_cycle — backfilled to 1 for existing
      rows.  Every production operation's first cycle is 1 (the
      model default), and no pre-migration labor row can
      meaningfully belong to a later cycle because the cycle
      contract didn't exist when those rows were written.  The
      backfill is truthful for the 0.9.x → 1.0 upgrade path.

    - nonconformance_reports.remediation_work_order_id — nullable
      FK to the remediation WO used when the original parent WO is
      already `closed` at NCR disposition time.  Most NCRs will
      not populate this field; it is the explicit opt-in for the
      closed-WO remediation branch.

  Neither column has a state-machine downstream beyond what
  services already encode, so no trigger or constraint beyond
  nullability + FK is added here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f91a43c5e2"
down_revision: str | Sequence[str] | None = "c0d5e871a4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _strip_op_rework_permission() -> None:
    """Remove op:rework from every role's permissions JSON array.

    GC-2 retires the transient rework state and its /rework route.
    Keeping op:rework seeded on engineer / qa_manager would leave
    the DB's effective-permissions set out of sync with
    ROLE_PERMISSIONS in auth.py — the same drift class the
    inspect:read migration (c4a7d2e91b38) already documented.
    The jsonb-dedupe idiom matches d68f3c2a10b9's precedent.
    """
    op.execute(
        sa.text("""
            UPDATE roles
            SET permissions = (
                cast(permissions AS jsonb) - :perm
            )::json
            WHERE cast(permissions AS jsonb) ? :perm
        """).bindparams(perm="op:rework")
    )


def upgrade() -> None:
    """Add execution_cycle to labor + remediation_work_order_id to NCR."""
    _strip_op_rework_permission()
    op.add_column(
        "labor_entries",
        sa.Column(
            "execution_cycle",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    # Drop the server default after backfill so new inserts come via
    # the model's explicit default (the service layer supplies the
    # current cycle on every create — we never want a silent 1).
    op.alter_column(
        "labor_entries", "execution_cycle", server_default=None,
    )

    op.add_column(
        "nonconformance_reports",
        sa.Column(
            "remediation_work_order_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey(
                "work_orders.id",
                name="fk_nonconformance_reports_remediation_wo",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Reverse the column additions."""
    op.drop_constraint(
        "fk_nonconformance_reports_remediation_wo",
        "nonconformance_reports",
        type_="foreignkey",
    )
    op.drop_column(
        "nonconformance_reports", "remediation_work_order_id",
    )
    op.drop_column("labor_entries", "execution_cycle")
