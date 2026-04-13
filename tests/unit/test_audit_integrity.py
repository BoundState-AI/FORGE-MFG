#!/usr/bin/env python3
"""
Module:   Test Audit Integrity
Purpose:  Unit and integration tests for audit hash-chain integrity.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The audit hash chain is a core integrity mechanism.  These tests verify
  that the hash computation covers all stored fields, that the genesis
  convention is correct, and that tampering with any field causes the
  verifier to detect it.  Integration tests verify the DB-level behavior;
  these unit tests verify the hash logic without a database.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from forge.audit.logger import (
    GENESIS_PREVIOUS_HASH,
    _compute_record_hash,
)

# ---------------------------------------------------------------------------
# Hash computation unit tests (no DB needed)
# ---------------------------------------------------------------------------

_SAMPLE_KWARGS = {
    "sequence_number": 1,
    "event_id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    "actor_type": "human",
    "user_id": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    "user_role": "operator",
    "timestamp": datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC),
    "action_type": "create",
    "entity_type": "work_order",
    "entity_id": uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
    "before_state": None,
    "after_state": {"status": "draft"},
    "ip_address": "10.0.0.1",
    "session_id": "sess-001",
    "previous_hash": GENESIS_PREVIOUS_HASH,
    "s3_replicated": False,
    "s3_replicated_at": None,
}


def test_hash_is_deterministic() -> None:
    """Same inputs must always produce the same hash."""
    h1 = _compute_record_hash(**_SAMPLE_KWARGS)  # type: ignore[arg-type]
    h2 = _compute_record_hash(**_SAMPLE_KWARGS)  # type: ignore[arg-type]
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_hash_is_64_hex_chars() -> None:
    """Record hash must be a 64-character lowercase hex SHA-256 digest."""
    h = _compute_record_hash(**_SAMPLE_KWARGS)  # type: ignore[arg-type]
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_genesis_previous_hash_is_64_zeros() -> None:
    """The genesis convention uses 64 zero hex chars as previous_hash."""
    assert GENESIS_PREVIOUS_HASH == "0" * 64
    assert len(GENESIS_PREVIOUS_HASH) == 64


def test_changing_any_field_changes_hash() -> None:
    """Modifying any single field must produce a different hash.

    Spec §3.1 hash coverage: "All stored fields are included in the
    hash input."  If changing a field doesn't change the hash, that
    field is not covered — a tamper-evidence gap.
    """
    base_hash = _compute_record_hash(**_SAMPLE_KWARGS)  # type: ignore[arg-type]

    # Every field that should be hashed
    field_overrides = {
        "sequence_number": 2,
        "event_id": uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        "actor_type": "system",
        "user_id": uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
        "user_role": "inspector",
        "timestamp": datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        "action_type": "update",
        "entity_type": "operation",
        "entity_id": uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
        "before_state": {"status": "released"},
        "after_state": {"status": "in_progress"},
        "ip_address": "192.168.1.1",
        "session_id": "sess-999",
        "previous_hash": "a" * 64,
        "s3_replicated": True,
        "s3_replicated_at": datetime(2026, 4, 13, 13, 0, 0, tzinfo=UTC),
    }

    for field_name, altered_value in field_overrides.items():
        kwargs = {**_SAMPLE_KWARGS, field_name: altered_value}
        altered_hash = _compute_record_hash(**kwargs)  # type: ignore[arg-type]
        assert altered_hash != base_hash, (
            f"Changing '{field_name}' did not change the hash — "
            f"field is not covered by hash computation"
        )


# ---------------------------------------------------------------------------
# Integration: chain + verifier against live DB
# ---------------------------------------------------------------------------

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")


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
@pytest.mark.asyncio
async def test_audit_chain_verifies_clean() -> None:
    """A chain built by create_audit_record must verify cleanly."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from forge.audit.integrity import verify_audit_chain
    from forge.audit.logger import create_audit_record
    from forge.models.enums import ActorType

    async_url = os.getenv(
        "FORGE_DATABASE_URL",
        "postgresql+asyncpg://forge:forge@localhost:5432/forge",
    )
    engine = create_async_engine(async_url)

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

    try:
        # Insert 3 records in the chain
        for i in range(3):
            async with AsyncSession(engine) as session, session.begin():
                await create_audit_record(
                    session,
                    event_id=uuid.uuid4(),
                    actor_type=ActorType.human,
                    user_id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                    user_role="operator",
                    action_type=f"test_integrity_{i}",
                    entity_type="work_order",
                    entity_id=uuid.uuid4(),
                    after_state={"step": i},
                )

        # Verify the chain
        async with AsyncSession(engine) as session:
            result = await verify_audit_chain(session)

        assert result.valid, f"Chain verification failed: {result.failures}"
        assert result.records_checked >= 3
    finally:
        # Cleanup — must disable trigger to delete audit records.
        # In a finally block so cleanup runs even on assertion failure.
        sync_engine = create_engine(_SYNC_URL)
        with sync_engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE audit_records DISABLE TRIGGER trg_protect_audit_records"
            ))
            conn.execute(text(
                "DELETE FROM audit_records WHERE action_type LIKE 'test_integrity_%'"
            ))
            conn.commit()
            conn.execute(text(
                "ALTER TABLE audit_records ENABLE TRIGGER trg_protect_audit_records"
            ))
            conn.commit()
        sync_engine.dispose()
        await engine.dispose()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
@pytest.mark.asyncio
async def test_tampered_record_detected() -> None:
    """Tampering with a hashed field must be detected by the verifier."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from forge.audit.integrity import verify_audit_chain
    from forge.audit.logger import create_audit_record
    from forge.models.enums import ActorType

    async_url = os.getenv(
        "FORGE_DATABASE_URL",
        "postgresql+asyncpg://forge:forge@localhost:5432/forge",
    )
    engine = create_async_engine(async_url)

    # Clean slate — must disable trigger to delete any stale audit records
    sync_engine = create_engine(_SYNC_URL)
    with sync_engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE audit_records DISABLE TRIGGER trg_protect_audit_records"
        ))
        conn.execute(text(
            "DELETE FROM audit_records WHERE action_type LIKE 'test_tamper_%'"
        ))
        conn.commit()
        conn.execute(text(
            "ALTER TABLE audit_records ENABLE TRIGGER trg_protect_audit_records"
        ))
        conn.commit()
    sync_engine.dispose()

    try:
        # Insert a record
        async with AsyncSession(engine) as session, session.begin():
            rec = await create_audit_record(
                session,
                event_id=uuid.uuid4(),
                actor_type=ActorType.human,
                user_id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                user_role="operator",
                action_type="test_tamper_original",
                entity_type="work_order",
                entity_id=uuid.uuid4(),
            )
            seq = rec.sequence_number

        # Tamper with it by disabling the trigger temporarily
        sync_engine = create_engine(_SYNC_URL)
        with sync_engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE audit_records "
                "DISABLE TRIGGER trg_protect_audit_records"
            ))
            conn.execute(text(
                "UPDATE audit_records SET action_type = 'test_tamper_HACKED'"
                " WHERE sequence_number = :seq"
            ).bindparams(seq=seq))
            conn.commit()
            conn.execute(text(
                "ALTER TABLE audit_records "
                "ENABLE TRIGGER trg_protect_audit_records"
            ))
            conn.commit()
        sync_engine.dispose()

        # Verify should detect the tamper
        async with AsyncSession(engine) as session:
            result = await verify_audit_chain(session)

        assert not result.valid
        hash_failures = [f for f in result.failures if f.field == "record_hash"]
        assert len(hash_failures) >= 1
    finally:
        # Cleanup — always runs, even on assertion failure
        sync_engine = create_engine(_SYNC_URL)
        with sync_engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE audit_records "
                "DISABLE TRIGGER trg_protect_audit_records"
            ))
            conn.execute(text(
                "DELETE FROM audit_records "
                "WHERE action_type LIKE 'test_tamper_%'"
            ))
            conn.commit()
            conn.execute(text(
                "ALTER TABLE audit_records "
                "ENABLE TRIGGER trg_protect_audit_records"
            ))
            conn.commit()
        sync_engine.dispose()
        await engine.dispose()
