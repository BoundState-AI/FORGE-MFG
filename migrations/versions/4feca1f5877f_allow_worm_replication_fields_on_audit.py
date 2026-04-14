#!/usr/bin/env python3
"""
Module:   Allow WORM Replication Fields on Audit
Purpose:  Relax the append-only audit trigger so the replication worker
          can set s3_replicated + s3_replicated_at on existing rows
          (spec §3.1, §7.2).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Revision ID: 4feca1f5877f
Revises: 46a86e0c1628
Create Date: 2026-04-14 03:00:00.000000

Why this migration:
  Slice 1C installed protect_audit_records() which blocks every UPDATE.
  Spec §3.1 AuditRecord explicitly lists `s3_replicated` and
  `s3_replicated_at` as fields updated after the initial insert; spec
  §7.2 requires a replicator that flips them when the row is shipped
  to S3 Object Lock.  The 1C trigger was correct for the integrity
  guarantee but over-restrictive for the WORM path.

  This migration redefines the trigger to allow UPDATE iff *only*
  s3_replicated / s3_replicated_at change.  Any other UPDATE — any
  DELETE — still raises restrict_violation.  Hash-chain fields,
  actor attribution, and content columns remain immutable.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4feca1f5877f"
down_revision: str | Sequence[str] | None = "46a86e0c1628"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace protect_audit_records() with a WORM-aware variant.

    DELETE is still blocked unconditionally.  UPDATE is blocked unless
    the only changed columns are s3_replicated / s3_replicated_at.
    """
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION protect_audit_records()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'Audit records are append-only and cannot be modified or deleted'
                    USING ERRCODE = 'restrict_violation';
            END IF;

            -- UPDATE: allow ONLY changes to s3_replicated / s3_replicated_at.
            -- Any other field change → restrict_violation.
            IF
                NEW.id IS DISTINCT FROM OLD.id
                OR NEW.sequence_number IS DISTINCT FROM OLD.sequence_number
                OR NEW.event_id IS DISTINCT FROM OLD.event_id
                OR NEW.actor_type IS DISTINCT FROM OLD.actor_type
                OR NEW.user_id IS DISTINCT FROM OLD.user_id
                OR NEW.user_role IS DISTINCT FROM OLD.user_role
                OR NEW.timestamp IS DISTINCT FROM OLD.timestamp
                OR NEW.action_type IS DISTINCT FROM OLD.action_type
                OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
                OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
                OR NEW.before_state::text IS DISTINCT FROM OLD.before_state::text
                OR NEW.after_state::text IS DISTINCT FROM OLD.after_state::text
                OR NEW.ip_address IS DISTINCT FROM OLD.ip_address
                OR NEW.session_id IS DISTINCT FROM OLD.session_id
                OR NEW.previous_hash IS DISTINCT FROM OLD.previous_hash
                OR NEW.record_hash IS DISTINCT FROM OLD.record_hash
            THEN
                RAISE EXCEPTION
                    'Audit records are append-only; only WORM replication fields may be updated'
                    USING ERRCODE = 'restrict_violation';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))


def downgrade() -> None:
    """Restore the strict UPDATE-prohibited trigger from slice 1C."""
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
