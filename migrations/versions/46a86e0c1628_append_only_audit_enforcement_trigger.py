#!/usr/bin/env python3
"""
Module:   Append-Only Audit Enforcement Migration
Purpose:  Add DB trigger to prevent UPDATE/DELETE on audit_records.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: 46a86e0c1628
Revises: 387cadd44f9d
Create Date: 2026-04-13 15:48:21.084758

Why this migration:
  CLAUDE.md architectural rule: "Audit records are append-only."
  Checklist 1C gate: "Audit rows cannot be updated or deleted."

  This trigger enforces append-only at the DB level — application bugs,
  direct SQL, or ORM misuse cannot bypass it.  Only INSERT is allowed;
  UPDATE and DELETE unconditionally raise restrict_violation.

  This is a forward migration (not an edit of a historical revision)
  because it adds new behavior on top of the existing schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "46a86e0c1628"
down_revision: str | Sequence[str] | None = "387cadd44f9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add append-only trigger to audit_records table."""
    # Unconditionally block UPDATE and DELETE — no field check needed.
    # Unlike protect_system_actor which conditionally blocks, audit
    # records are ALWAYS immutable once inserted.
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION protect_audit_records()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'Audit records are append-only and cannot be modified or deleted'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_protect_audit_records
        BEFORE UPDATE OR DELETE ON audit_records
        FOR EACH ROW
        EXECUTE FUNCTION protect_audit_records();
    """))


def downgrade() -> None:
    """Remove append-only trigger from audit_records."""
    op.execute(sa.text(
        "DROP TRIGGER IF EXISTS trg_protect_audit_records ON audit_records"
    ))
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS protect_audit_records()"
    ))
