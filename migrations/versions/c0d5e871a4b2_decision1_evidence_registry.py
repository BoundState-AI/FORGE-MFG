#!/usr/bin/env python3
"""
Module:   Decision 1 Evidence Registry Migration
Purpose:  Build spec v1.4.2 Decision 1 — introduce the
          EvidenceConfirmation registry, migrate inspection_events
          evidence_refs to structured JSONB, and add the NCR
          evidence_refs column.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: c0d5e871a4b2
Revises: a42f81b3d570
Create Date: 2026-04-15 22:00:00.000000

Why this migration:
  Decision 1 keeps evidence-ref lists on the parent entities but
  adds an internal registry of confirmed uploads as the durable
  source of truth.  Three structural changes land together because
  the new write contract depends on all three being present:

    1. evidence_confirmations table — the registry rows the
       service layer reads to hydrate metadata on inspection and
       NCR writes.
    2. inspection_events.evidence_refs → JSONB.  Legacy
       ARRAY(text) rows are migrated in-place: each bare s3_key
       string becomes {"s3_key": <key>} so the read path does not
       need a branching shape.  Structured metadata for pre-
       migration rows is intentionally sparse — the legacy rows
       predate the registry contract and have no durable
       provenance to hydrate from.
    3. nonconformance_reports.evidence_refs JSONB column added
       with empty-array default so the NCR close evidence gate
       has a real column to read.

  FK from evidence_confirmations references programs and users,
  matching every other attribution surface in the schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c0d5e871a4b2"
down_revision: str | Sequence[str] | None = "a42f81b3d570"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Postgres-dialect ENUM with create_type=False reliably avoids
# implicit CREATE TYPE emission from op.create_table — the generic
# sa.Enum was observed to still issue the DDL on some SQLAlchemy
# versions.  The explicit DO-block above owns lifecycle.
EVIDENCE_SCOPE_ENUM = PG_ENUM(
    "inspection_evidence",
    "ncr_evidence",
    name="evidencescope",
    create_type=False,
)


def upgrade() -> None:
    """Create registry table + migrate inspection refs + add NCR column."""

    # -----------------------------------------------------------------
    # Part 1: evidence_confirmations table.
    # -----------------------------------------------------------------
    # Idempotent CREATE via pg DO block — SQLAlchemy's checkfirst
    # has been observed to miss-detect an existing type in some
    # snapshot-restore paths; a pg_catalog check in a DO $$ EXCEPTION
    # handler is the canonical idempotent pattern.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE evidencescope AS ENUM (
                'inspection_evidence', 'ncr_evidence'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.create_table(
        "evidence_confirmations",
        sa.Column("s3_key", sa.String(length=512), primary_key=True),
        sa.Column(
            "program_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("programs.id"),
            nullable=False,
        ),
        sa.Column(
            "scope",
            EVIDENCE_SCOPE_ENUM,
            nullable=False,
        ),
        sa.Column(
            "uploaded_by",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "pending_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Index the (program_id, scope) pair — cleanup and hydration
    # both filter on this combo.
    op.create_index(
        "ix_evidence_confirmations_program_scope",
        "evidence_confirmations",
        ["program_id", "scope"],
    )

    # -----------------------------------------------------------------
    # Part 2: inspection_events.evidence_refs → JSONB
    # -----------------------------------------------------------------
    # Migrate-in-place: ARRAY(text) of bare s3_keys becomes a JSONB
    # array of {s3_key: <key>} objects.  `USING` expression is
    # Postgres-specific and safe because the column is new enough
    # that no custom collations or triggers depend on the array shape.
    # Register an immutable helper function that transforms the
    # legacy text[] shape into the structured JSONB list.  Inline
    # subqueries in USING are not allowed by Postgres' ALTER COLUMN
    # contract, so we lift the transform into a function.  The
    # function is created STRICT + IMMUTABLE so the planner can
    # inline it during the rewrite.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION _forge_evidence_legacy_to_jsonb(
            keys text[]
        ) RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
            SELECT COALESCE(
                (
                    SELECT jsonb_agg(jsonb_build_object('s3_key', k))
                    FROM unnest(keys) AS k
                ),
                '[]'::jsonb
            );
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE inspection_events
        ALTER COLUMN evidence_refs TYPE JSONB
        USING _forge_evidence_legacy_to_jsonb(evidence_refs)
        """
    )
    op.execute("DROP FUNCTION _forge_evidence_legacy_to_jsonb(text[])")
    op.alter_column(
        "inspection_events",
        "evidence_refs",
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
    )

    # -----------------------------------------------------------------
    # Part 3: nonconformance_reports.evidence_refs JSONB column.
    # -----------------------------------------------------------------
    op.add_column(
        "nonconformance_reports",
        sa.Column(
            "evidence_refs",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Reverse the three changes in opposite order."""
    op.drop_column("nonconformance_reports", "evidence_refs")
    # The PG_ENUM instance up top carries create_type=False; the
    # downgrade owns explicit DROP TYPE via SQL so the enum name
    # is cleaned up even if the table drop below did not cascade.
    _ = PG_ENUM  # retain import reference for clarity

    # Convert inspection_events.evidence_refs back to text[] of s3_keys.
    # Drop the default first because ARRAY cannot accept the JSONB
    # default expression.
    op.alter_column(
        "inspection_events",
        "evidence_refs",
        server_default=None,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION _forge_evidence_jsonb_to_legacy(
            refs jsonb
        ) RETURNS text[] LANGUAGE sql IMMUTABLE AS $$
            SELECT COALESCE(
                (
                    SELECT array_agg(ref->>'s3_key')
                    FROM jsonb_array_elements(refs) AS ref
                ),
                ARRAY[]::text[]
            );
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE inspection_events
        ALTER COLUMN evidence_refs TYPE text[]
        USING _forge_evidence_jsonb_to_legacy(evidence_refs)
        """
    )
    op.execute("DROP FUNCTION _forge_evidence_jsonb_to_legacy(jsonb)")

    op.drop_index(
        "ix_evidence_confirmations_program_scope",
        table_name="evidence_confirmations",
    )
    op.drop_table("evidence_confirmations")
    op.execute("DROP TYPE IF EXISTS evidencescope")
