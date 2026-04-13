#!/usr/bin/env python3
"""
Module:   Test System Actor
Purpose:  Verify SYSTEM actor immutability is enforced at the database level.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this test:
  Spec §3.1 states the SYSTEM actor is immutable after provisioning.
  The immutability trigger (protect_system_actor) must block both
  UPDATE and DELETE operations on any row where is_system_actor = true.
  This test runs against a live Postgres instance to verify the trigger
  fires correctly — unit tests cannot validate DB triggers.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

# This test requires a live Postgres connection.
# Skip if the database is not available.
_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def _db_available() -> bool:
    """Check if the test database is reachable."""
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestSystemActorImmutability:
    """Verify the protect_system_actor trigger blocks mutations."""

    @pytest.fixture(autouse=True)
    def _engine(self) -> Generator[sa.engine.Engine]:
        self.engine = create_engine(_SYNC_URL)
        yield self.engine
        self.engine.dispose()

    def test_update_system_actor_blocked(self) -> None:
        """UPDATE on is_system_actor=true must raise restrict_violation."""
        with (
            self.engine.connect() as conn,
            pytest.raises(sa.exc.IntegrityError, match="immutable"),
        ):
            conn.execute(text(
                "UPDATE users SET display_name = 'HACKED' "
                "WHERE id = :id"
            ).bindparams(id=SYSTEM_USER_ID))
            conn.commit()

    def test_delete_system_actor_blocked(self) -> None:
        """DELETE on is_system_actor=true must raise restrict_violation."""
        with (
            self.engine.connect() as conn,
            pytest.raises(sa.exc.IntegrityError, match="immutable"),
        ):
            conn.execute(text(
                "DELETE FROM users WHERE id = :id"
            ).bindparams(id=SYSTEM_USER_ID))
            conn.commit()

    def test_update_normal_user_allowed(self) -> None:
        """UPDATE on a non-system-actor user must succeed.

        The trigger must only block mutations on is_system_actor=true
        rows.  Normal users must be freely updatable.
        """
        temp_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        with self.engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO users "
                "(id, cognito_sub, email, display_name, "
                " is_system_actor, active, created_by, updated_by) "
                "VALUES (:id, 'test_update', 'upd@test.com', 'Before', "
                "        false, true, :sys, :sys)"
            ).bindparams(id=temp_id, sys=SYSTEM_USER_ID))
            conn.execute(text(
                "UPDATE users SET display_name = 'After' WHERE id = :id"
            ).bindparams(id=temp_id))
            row = conn.execute(text(
                "SELECT display_name FROM users WHERE id = :id"
            ).bindparams(id=temp_id)).fetchone()
            assert row is not None
            assert row[0] == "After"
            # Clean up
            conn.execute(text(
                "DELETE FROM users WHERE id = :id"
            ).bindparams(id=temp_id))
            conn.commit()

    def test_delete_normal_user_allowed(self) -> None:
        """DELETE on a non-system-actor user must succeed.

        A BEFORE DELETE trigger that returns NULL silently suppresses
        the delete.  The trigger must return OLD for non-system rows
        so normal deletes proceed.
        """
        temp_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        with self.engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO users "
                "(id, cognito_sub, email, display_name, "
                " is_system_actor, active, created_by, updated_by) "
                "VALUES (:id, 'test_delete', 'del@test.com', 'TempDel', "
                "        false, true, :sys, :sys)"
            ).bindparams(id=temp_id, sys=SYSTEM_USER_ID))
            conn.execute(text(
                "DELETE FROM users WHERE id = :id"
            ).bindparams(id=temp_id))
            row = conn.execute(text(
                "SELECT id FROM users WHERE id = :id"
            ).bindparams(id=temp_id)).fetchone()
            assert row is None, "Non-system user should be deletable"
            conn.commit()
