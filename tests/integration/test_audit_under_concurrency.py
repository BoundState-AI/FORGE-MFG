#!/usr/bin/env python3
"""
Module:   Test Audit Under Concurrency
Purpose:  Verify advisory-lock serialization prevents hash-chain forks.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Spec §7.1: "Advisory lock (pg_advisory_xact_lock) serializes record
  insertion."  Without the advisory lock, concurrent writers could read
  the same latest sequence_number and fork the chain.  This test spawns
  multiple concurrent audit writes and verifies the chain remains linear
  with unique sequence numbers and valid previous_hash links.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from forge.audit.integrity import verify_audit_chain
from forge.audit.logger import create_audit_record
from forge.models.enums import ActorType

_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)

_SYNC_URL = _ASYNC_URL.replace("+asyncpg", "")


def _db_available() -> bool:
    from sqlalchemy import create_engine

    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
@pytest.mark.asyncio
async def test_concurrent_audit_writes_do_not_fork_chain() -> None:
    """10 concurrent audit writes must produce a linear, valid chain.

    Without advisory locking, concurrent writers would read the same
    latest sequence_number and previous_hash, creating duplicate
    sequences and forked chains.  This test proves serialization works.
    """
    engine = create_async_engine(_ASYNC_URL)

    # Clean ALL audit records for a fresh chain (verifier checks the whole chain)
    from sqlalchemy import create_engine as _create_sync_engine

    sync_eng = _create_sync_engine(_SYNC_URL)
    with sync_eng.connect() as conn:
        conn.execute(text(
            "ALTER TABLE audit_records DISABLE TRIGGER trg_protect_audit_records"
        ))
        conn.execute(text("DELETE FROM audit_records"))
        conn.commit()
        conn.execute(text(
            "ALTER TABLE audit_records ENABLE TRIGGER trg_protect_audit_records"
        ))
        conn.commit()
    sync_eng.dispose()

    async def _write_audit(index: int) -> None:
        """Insert one audit record in its own transaction."""
        async with AsyncSession(engine) as session, session.begin():
            await create_audit_record(
                session,
                event_id=uuid.uuid4(),
                actor_type=ActorType.system,
                user_id=None,
                user_role=None,
                action_type=f"test_concurrent_{index}",
                entity_type="work_order",
                entity_id=uuid.uuid4(),
            )

    # Fire 10 concurrent writers
    await asyncio.gather(*[_write_audit(i) for i in range(10)])

    # Verify the chain is valid
    async with AsyncSession(engine) as session:
        result = await verify_audit_chain(session)

    assert result.valid, f"Chain forked under concurrency: {result.failures}"

    # Verify all 10 records exist with unique sequence numbers
    async with AsyncSession(engine) as session:
        rows = await session.execute(text(
            "SELECT sequence_number FROM audit_records"
            " WHERE action_type LIKE 'test_concurrent_%'"
            " ORDER BY sequence_number"
        ))
        seqs = [r[0] for r in rows.fetchall()]

    assert len(seqs) == 10
    assert len(set(seqs)) == 10  # all unique

    # Cleanup — must disable trigger to delete audit records
    sync_eng = _create_sync_engine(_SYNC_URL)
    with sync_eng.connect() as conn:
        conn.execute(text(
            "ALTER TABLE audit_records DISABLE TRIGGER trg_protect_audit_records"
        ))
        conn.execute(text(
            "DELETE FROM audit_records"
            " WHERE action_type LIKE 'test_concurrent_%'"
        ))
        conn.commit()
        conn.execute(text(
            "ALTER TABLE audit_records ENABLE TRIGGER trg_protect_audit_records"
        ))
        conn.commit()
    sync_eng.dispose()

    await engine.dispose()
