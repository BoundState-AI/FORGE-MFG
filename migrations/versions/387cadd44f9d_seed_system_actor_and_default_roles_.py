#!/usr/bin/env python3
"""
Module:   Seed Migration
Purpose:  Provision the SYSTEM actor, default roles, and immutability trigger.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Revision ID: 387cadd44f9d
Revises: f9b58a1bcb85
Create Date: 2026-04-13 14:36:11.686290

Why this migration:
  The SYSTEM actor (spec §3.1) is a reserved User record that background
  workers use when creating audit records.  It cannot authenticate via
  Cognito and is immutable after provisioning.

  Immutability is enforced by a Postgres trigger that blocks UPDATE and
  DELETE on any row where is_system_actor = true.  This is durable
  protection — it cannot be bypassed by application bugs or direct SQL
  unless the trigger is explicitly dropped.

  The six roles (spec §5.0.2) are seeded with their full permission
  arrays so the authorization system works from first boot.

  A deterministic UUID is used for the SYSTEM user so that application
  code (e.g. the audit logger) can reference it by constant without a
  database lookup.  The string form is used directly because Alembic's
  sa.text() binds raw values to the SQL driver.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "387cadd44f9d"
down_revision: str | Sequence[str] | None = "f9b58a1bcb85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Deterministic UUID — stable across environments, referenced by app code
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"

# Role permission mapping from spec §5.0.2
ROLES: dict[str, list[str]] = {
    "operator": [
        "op:execute", "labor:create", "labor:update_own", "mat:consume",
        "part:create", "part:update", "wo:read", "op:read", "ncr:read",
        "part:read", "doc:read", "sched:read", "dash:read", "chat:query",
    ],
    "inspector": [
        "inspect:create", "inspect:sign_off", "mat:receive", "mat:accept",
        "ncr:create", "doc:read", "doc:upload", "wo:read", "op:read",
        "ncr:read", "part:read", "sched:read", "dash:read", "chat:query",
    ],
    "engineer": [
        "wo:create", "wo:update", "wo:release", "wo:hold", "wo:resume",
        "wo:complete", "op:create", "op:update", "op:delete", "op:hold",
        "op:rework", "ncr:create", "ncr:disposition", "sched:create",
        "sched:baseline", "sched:what_if", "labor:update_any", "doc:create",
        "doc:read", "doc:upload", "doc:finalize", "wo:read", "op:read",
        "ncr:read", "part:read", "sched:read", "dash:read", "chat:query",
    ],
    "qa_manager": [
        # All engineer permissions PLUS wo:close and ncr:close
        "wo:create", "wo:update", "wo:release", "wo:hold", "wo:resume",
        "wo:complete", "wo:close", "op:create", "op:update", "op:delete",
        "op:hold", "op:rework", "ncr:create", "ncr:disposition", "ncr:close",
        "sched:create", "sched:baseline", "sched:what_if", "labor:update_any",
        "doc:create", "doc:read", "doc:upload", "doc:finalize", "wo:read",
        "op:read", "ncr:read", "part:read", "sched:read", "dash:read",
        "chat:query",
    ],
    "program_manager": [
        "wo:read", "op:read", "ncr:read", "part:read", "doc:read",
        "sched:read", "dash:read", "chat:query",
    ],
    "admin": [
        "admin:manage", "wo:read", "op:read", "ncr:read", "part:read",
        "doc:read", "sched:read", "dash:read",
    ],
}


def upgrade() -> None:
    """Seed SYSTEM actor, default roles, and immutability trigger."""
    # --- SYSTEM actor immutability trigger ---
    # Prevents UPDATE and DELETE on rows where is_system_actor = true.
    # This is the authoritative enforcement — application-level checks
    # are defense-in-depth, not the source of truth.
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION protect_system_actor()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.is_system_actor = true THEN
                RAISE EXCEPTION
                    'System actor records are immutable and cannot be modified or deleted'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            -- For UPDATE, return NEW to allow the modification.
            -- For DELETE, NEW is NULL so we must return OLD to allow
            -- the delete to proceed on non-system-actor rows.
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_protect_system_actor
        BEFORE UPDATE OR DELETE ON users
        FOR EACH ROW
        EXECUTE FUNCTION protect_system_actor();
    """))

    # --- SYSTEM actor seed ---
    # Self-referential created_by/updated_by (bootstrap).
    # Trigger is installed first, but INSERT is not blocked — only
    # UPDATE and DELETE are.  The trigger checks OLD.is_system_actor,
    # which doesn't exist on INSERT.
    op.execute(
        sa.text("""
            INSERT INTO users (id, cognito_sub, email, display_name,
                               is_system_actor, active, created_by, updated_by)
            VALUES (:id, 'SYSTEM', 'system@forge.internal', 'SYSTEM',
                    true, true, :id, :id)
            ON CONFLICT (cognito_sub) DO NOTHING
        """).bindparams(id=SYSTEM_USER_ID)
    )

    # --- Default roles seed ---
    # Permissions stored as JSON arrays per spec §3.1.
    # created_by/updated_by reference the SYSTEM actor.
    for role_name, permissions in ROLES.items():
        op.execute(
            sa.text("""
                INSERT INTO roles (id, name, permissions, created_by, updated_by)
                VALUES (gen_random_uuid(), :name, CAST(:perms AS jsonb),
                        :system_id, :system_id)
                ON CONFLICT (name) DO NOTHING
            """).bindparams(
                name=role_name,
                perms=_json_encode(permissions),
                system_id=SYSTEM_USER_ID,
            )
        )


def downgrade() -> None:
    """Remove seeded data and immutability trigger."""
    # Must drop trigger before deleting SYSTEM actor
    op.execute(sa.text(
        "DROP TRIGGER IF EXISTS trg_protect_system_actor ON users"
    ))
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS protect_system_actor()"
    ))
    for role_name in ROLES:
        op.execute(
            sa.text("DELETE FROM roles WHERE name = :name").bindparams(name=role_name)
        )
    op.execute(
        sa.text("DELETE FROM users WHERE id = :id").bindparams(id=SYSTEM_USER_ID)
    )


def _json_encode(value: list[str]) -> str:
    """Encode a Python list as a JSON string for SQL binding."""
    import json
    return json.dumps(value)
