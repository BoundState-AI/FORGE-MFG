#!/usr/bin/env python3
"""
Module:   Test Optimistic Locking
Purpose:  Prove version-conflict detection for the AuditableMixin version column.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why these tests:
  Spec §5.15: "Version mismatch returns 409 Conflict with current entity
  state."  These tests verify the check_version / increment_version
  infrastructure using a real ORM entity (Program, which has
  AuditableMixin).  Both the conflict path (stale version → error) and
  the success path (correct version → increment) are tested.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from forge.models.program import Program
from forge.services.versioning import (
    VersionConflictError,
    check_version,
    increment_version,
    version_conflict_response,
)

_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)
_SYNC_URL = _ASYNC_URL.replace("+asyncpg", "")
_SYSTEM_ID = "00000000-0000-0000-0000-000000000001"


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


def test_check_version_passes_on_match() -> None:
    """Correct version does not raise — the update may proceed."""
    p = Program()
    p.version = 3
    p.id = uuid.uuid4()
    check_version(p, expected_version=3, entity_type="Program")


def test_check_version_raises_on_mismatch() -> None:
    """Stale version raises VersionConflictError.

    Spec §5.15: "Version mismatch returns 409 Conflict."
    """
    p = Program()
    p.version = 5
    p.id = uuid.uuid4()
    with pytest.raises(VersionConflictError) as exc_info:
        check_version(p, expected_version=3, entity_type="Program")
    assert exc_info.value.current_version == 5


def test_increment_version() -> None:
    """increment_version advances the counter by 1."""
    p = Program()
    p.version = 1
    p.id = uuid.uuid4()
    increment_version(p)
    assert p.version == 2


def test_conflict_response_is_409() -> None:
    """VersionConflictError converts to a 409 HTTPException."""
    err = VersionConflictError("Program", "abc-123", current_version=5)
    exc = version_conflict_response(err)
    assert exc.status_code == 409
    assert isinstance(exc.detail, dict)
    assert exc.detail["current_version"] == 5


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
@pytest.mark.asyncio
async def test_optimistic_lock_round_trip() -> None:
    """Full round-trip: load entity, check version, mutate, increment, save.

    Proves the optimistic locking infrastructure works end-to-end against
    a real database with a real ORM entity.
    """
    engine = create_async_engine(_ASYNC_URL)
    test_id = uuid.uuid4()
    # Use a unique code to avoid conflicts with stale data from prior runs
    unique_code = f"OL-{test_id.hex[:8]}"

    # Clean any stale test data first
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(text(
            "DELETE FROM programs WHERE code LIKE 'OL-%'"
        ))

    # Insert a test program
    async with AsyncSession(engine) as session, session.begin():
        prog = Program(
            id=test_id,
            code=unique_code,
            name="Optimistic Lock Test",
            active=True,
            created_by=uuid.UUID(_SYSTEM_ID),
            updated_by=uuid.UUID(_SYSTEM_ID),
        )
        session.add(prog)

    # Load, check, mutate, save
    async with AsyncSession(engine) as session, session.begin():
        loaded = await session.get(Program, test_id)
        assert loaded is not None
        assert loaded.version == 1

        check_version(loaded, expected_version=1, entity_type="Program")
        loaded.name = "Updated Name"
        increment_version(loaded)

    # Reload and verify
    async with AsyncSession(engine) as session:
        reloaded = await session.get(Program, test_id)
        assert reloaded is not None
        assert reloaded.name == "Updated Name"
        assert reloaded.version == 2

    # Now try with stale version — should raise
    async with AsyncSession(engine) as session:
        stale = await session.get(Program, test_id)
        assert stale is not None
        with pytest.raises(VersionConflictError):
            check_version(stale, expected_version=1, entity_type="Program")

    # Cleanup
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(text(
            "DELETE FROM programs WHERE id = CAST(:pid AS uuid)"
        ).bindparams(pid=str(test_id)))

    await engine.dispose()
