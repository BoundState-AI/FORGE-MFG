#!/usr/bin/env python3
"""
Module:   Test Audit Append-Only
Purpose:  Verify DB trigger blocks UPDATE/DELETE on audit_records while
          allowing normal INSERTs.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  CLAUDE.md: "Audit records are append-only."
  Checklist 1C: "Audit rows cannot be updated or deleted."
  The protect_audit_records trigger enforces this at the DB level.
  These tests verify both the blocked paths (update, delete) and the
  allowed path (insert) against a live database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")

_SYSTEM_ID = "00000000-0000-0000-0000-000000000001"


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestAuditAppendOnly:
    """Verify append-only trigger on audit_records."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        self.engine = create_engine(_SYNC_URL)
        # Insert a test audit record
        self.test_id = str(uuid.uuid4())
        with self.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO audit_records
                (id, sequence_number, event_id, actor_type, user_id,
                 timestamp, action_type, entity_type, entity_id,
                 previous_hash, record_hash, s3_replicated)
                VALUES (:id, -999, :eid, 'system', :uid,
                        :ts, 'test_append_only', 'test', :eid,
                        :ph, :rh, false)
            """).bindparams(
                id=self.test_id,
                eid=str(uuid.uuid4()),
                uid=_SYSTEM_ID,
                ts=datetime.now(UTC),
                ph="0" * 64,
                rh="a" * 64,
            ))
            conn.commit()
        yield
        # Cleanup: must disable trigger to delete test data
        with self.engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE audit_records "
                "DISABLE TRIGGER trg_protect_audit_records"
            ))
            conn.execute(text(
                "DELETE FROM audit_records WHERE id = :id"
            ).bindparams(id=self.test_id))
            conn.commit()
            conn.execute(text(
                "ALTER TABLE audit_records "
                "ENABLE TRIGGER trg_protect_audit_records"
            ))
            conn.commit()
        self.engine.dispose()

    def test_insert_allowed(self) -> None:
        """Normal INSERT into audit_records must succeed."""
        new_id = str(uuid.uuid4())
        with self.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO audit_records
                (id, sequence_number, event_id, actor_type,
                 timestamp, action_type, entity_type, entity_id,
                 previous_hash, record_hash, s3_replicated)
                VALUES (:id, -998, :eid, 'system',
                        :ts, 'test_insert_ok', 'test', :eid,
                        :ph, :rh, false)
            """).bindparams(
                id=new_id,
                eid=str(uuid.uuid4()),
                ts=datetime.now(UTC),
                ph="0" * 64,
                rh="b" * 64,
            ))
            conn.commit()
            # Verify it exists
            row = conn.execute(text(
                "SELECT id FROM audit_records WHERE id = :id"
            ).bindparams(id=new_id)).fetchone()
            assert row is not None

        # Cleanup
        with self.engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE audit_records "
                "DISABLE TRIGGER trg_protect_audit_records"
            ))
            conn.execute(text(
                "DELETE FROM audit_records WHERE id = :id"
            ).bindparams(id=new_id))
            conn.commit()
            conn.execute(text(
                "ALTER TABLE audit_records "
                "ENABLE TRIGGER trg_protect_audit_records"
            ))
            conn.commit()

    def test_update_blocked(self) -> None:
        """UPDATE on audit_records must raise restrict_violation."""
        with (
            self.engine.connect() as conn,
            pytest.raises(sa.exc.IntegrityError, match="append-only"),
        ):
            conn.execute(text(
                "UPDATE audit_records SET action_type = 'HACKED'"
                " WHERE id = :id"
            ).bindparams(id=self.test_id))
            conn.commit()

    def test_delete_blocked(self) -> None:
        """DELETE on audit_records must raise restrict_violation."""
        with (
            self.engine.connect() as conn,
            pytest.raises(sa.exc.IntegrityError, match="append-only"),
        ):
            conn.execute(text(
                "DELETE FROM audit_records WHERE id = :id"
            ).bindparams(id=self.test_id))
            conn.commit()
