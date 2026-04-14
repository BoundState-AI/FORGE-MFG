#!/usr/bin/env python3
"""
Module:   Test WORM Replicator
Purpose:  Unit tests for the audit → S3 Object Lock replicator
          (spec §7.2 [CONDITIONAL]).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  Spec §7.2 sets a 5-minute replication SLA with exponential backoff
  and max 10 attempts.  These tests exercise the success path (row
  marked replicated, sink received deterministic key/body) and the
  retry path (failure raises, attempt tracker advances, ceiling
  at max_attempts).  The sink seam lets tests assert without any
  network IO.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from forge.config import Settings
from forge.services import worm_replicator

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")

_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _insert_audit_row() -> str:
    """Insert a single audit row marked s3_replicated=False.

    We disable the protect_audit_records trigger so the test harness
    can seed rows without going through the full create_audit_record
    helper (which we already exercise elsewhere).
    """
    rid = str(uuid.uuid4())
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE audit_records "
            "DISABLE TRIGGER trg_protect_audit_records"
        ))
        # Pick a unique sequence number higher than any existing row.
        max_seq = conn.execute(text(
            "SELECT COALESCE(MAX(sequence_number), 0) FROM audit_records"
        )).scalar() or 0
        new_seq = int(max_seq) + 10_000 + int(rid[:4], 16) % 10_000
        conn.execute(text("""
            INSERT INTO audit_records
              (id, sequence_number, event_id, actor_type, user_id,
               user_role, timestamp, action_type, entity_type,
               entity_id, before_state, after_state, ip_address,
               session_id, previous_hash, record_hash,
               s3_replicated, s3_replicated_at)
            VALUES
              (CAST(:id AS uuid), :seq, CAST(:eid AS uuid), 'system',
               NULL, NULL, :ts, 'test.worm', 'test_entity',
               CAST(:eid AS uuid), NULL, NULL, NULL, NULL,
               '0000000000000000000000000000000000000000000000000000000000000000',
               '1111111111111111111111111111111111111111111111111111111111111111',
               false, NULL)
        """).bindparams(
            id=rid, seq=new_seq, eid=str(uuid.uuid4()),
            ts=datetime.now(UTC),
        ))
        conn.execute(text(
            "ALTER TABLE audit_records "
            "ENABLE TRIGGER trg_protect_audit_records"
        ))
        conn.commit()
    engine.dispose()
    return rid


def _row_after(rid: str) -> dict[str, Any]:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT s3_replicated, s3_replicated_at "
            "FROM audit_records WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=rid)).fetchone()
    engine.dispose()
    assert row is not None
    return {
        "s3_replicated": row[0], "s3_replicated_at": row[1],
    }


def _clear_worm_attempts() -> None:
    """Wipe the sidecar table so each test starts from zero."""
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM worm_replication_attempts"))
        conn.commit()
    engine.dispose()


def _attempt_row(rid: str) -> dict[str, Any] | None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT attempt_count, next_attempt_at, last_error "
            "FROM worm_replication_attempts "
            "WHERE audit_record_id = CAST(:id AS uuid)"
        ).bindparams(id=rid)).fetchone()
    engine.dispose()
    if row is None:
        return None
    return {
        "attempt_count": row[0], "next_attempt_at": row[1],
        "last_error": row[2],
    }


def _delete_audit_row(rid: str) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE audit_records "
            "DISABLE TRIGGER trg_protect_audit_records"
        ))
        conn.execute(text(
            "DELETE FROM audit_records WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=rid))
        conn.execute(text(
            "ALTER TABLE audit_records "
            "ENABLE TRIGGER trg_protect_audit_records"
        ))
        conn.commit()
    engine.dispose()


async def _async_session() -> AsyncSession:
    engine = create_async_engine(_ASYNC_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory()


def _settings() -> Settings:
    return Settings(auth_mode="local", worm_max_attempts=3)


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestWormReplicator:
    """Real-DB tests for WORM replication paths."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        _clear_worm_attempts()
        self._rids: list[str] = []
        yield
        for rid in self._rids:
            _delete_audit_row(rid)
        _clear_worm_attempts()

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_process_once_replicates_successfully(self) -> None:
        """Happy path: row flipped to s3_replicated=True with timestamp."""
        rid = _insert_audit_row()
        self._rids.append(rid)

        sink = worm_replicator.StubSink()
        session = await _async_session()
        try:
            replicated = await worm_replicator.process_once(
                session, settings=_settings(), sink=sink,
            )
        finally:
            await session.close()

        assert replicated >= 1
        row = _row_after(rid)
        assert row["s3_replicated"] is True
        assert row["s3_replicated_at"] is not None
        # The stub sink recorded the PUT with a deterministic key.
        assert len(sink.calls) >= 1
        first_key = sink.calls[0][0]
        assert first_key.startswith("audit/")
        assert first_key.endswith(".json")

    # ------------------------------------------------------------------
    # Retry + backoff
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_failed_sink_does_not_mark_replicated(self) -> None:
        """Sink raises → row stays s3_replicated=False, attempt tracked."""
        rid = _insert_audit_row()
        self._rids.append(rid)

        sink = worm_replicator.StubSink()
        sink.raise_on_call = True
        session = await _async_session()
        try:
            replicated = await worm_replicator.process_once(
                session, settings=_settings(), sink=sink,
            )
        finally:
            await session.close()
        assert replicated == 0
        row = _row_after(rid)
        assert row["s3_replicated"] is False
        assert row["s3_replicated_at"] is None

    @pytest.mark.asyncio
    async def test_dead_letter_after_max_attempts(self) -> None:
        """After max_attempts failures the sidecar is at the ceiling and
        the row stays unreplicated.  Spec §7.2 allows manual intervention."""
        rid = _insert_audit_row()
        self._rids.append(rid)

        settings = _settings()
        sink = worm_replicator.StubSink()
        sink.raise_on_call = True
        session = await _async_session()
        try:
            # Exactly max_attempts ticks — each clears next_attempt_at
            # so the backoff window does not stall the test.  After the
            # ceiling is hit the claim query will skip the row by
            # attempt_count even if next_attempt_at is in the past.
            for _ in range(settings.worm_max_attempts):
                engine = create_engine(_SYNC_URL)
                with engine.connect() as conn:
                    conn.execute(text(
                        "UPDATE worm_replication_attempts "
                        "SET next_attempt_at = NULL "
                        "WHERE audit_record_id = CAST(:id AS uuid)"
                    ).bindparams(id=rid))
                    conn.commit()
                engine.dispose()
                await worm_replicator.process_once(
                    session, settings=settings, sink=sink,
                )
        finally:
            await session.close()

        sidecar = _attempt_row(rid)
        assert sidecar is not None
        assert sidecar["attempt_count"] == settings.worm_max_attempts
        row = _row_after(rid)
        assert row["s3_replicated"] is False

    @pytest.mark.asyncio
    async def test_maxed_out_row_is_not_reclaimed(self) -> None:
        """4B review fix: _claim_batch must skip rows at max_attempts.

        Seeds a sidecar row with attempt_count = worm_max_attempts and
        next_attempt_at in the past.  A naive claim query would pick
        the row up again; the correct query excludes it via the
        attempt_count < max_attempts filter.  Runs two process ticks
        and asserts the sink was never called.
        """
        rid = _insert_audit_row()
        self._rids.append(rid)

        settings = _settings()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO worm_replication_attempts
                  (audit_record_id, attempt_count, next_attempt_at,
                   last_attempt_at, last_error)
                VALUES
                  (CAST(:id AS uuid), :ceil, :past, :past, 'prior failure')
            """).bindparams(
                id=rid,
                ceil=settings.worm_max_attempts,
                past=datetime.now(UTC) - timedelta(hours=1),
            ))
            conn.commit()
        engine.dispose()

        sink = worm_replicator.StubSink()
        session = await _async_session()
        try:
            r1 = await worm_replicator.process_once(
                session, settings=settings, sink=sink,
            )
            r2 = await worm_replicator.process_once(
                session, settings=settings, sink=sink,
            )
        finally:
            await session.close()

        assert r1 == 0
        assert r2 == 0
        # Sink was never invoked — proves the claim query skipped
        # the row, not that _replicate_one's defense-in-depth guard
        # bailed out after a sink call.
        assert sink.calls == []
        assert _row_after(rid)["s3_replicated"] is False
        sidecar = _attempt_row(rid)
        assert sidecar is not None
        assert sidecar["attempt_count"] == settings.worm_max_attempts

    @pytest.mark.asyncio
    async def test_attempt_state_survives_process_restart(self) -> None:
        """Durability gate: the sidecar retains attempts across sessions.

        Simulates a worker restart by closing the first session after a
        failure and opening a fresh one.  The next attempt must
        increment from the persisted count, not start at zero — if this
        test fails, a real restart could re-try a row that is already
        at the ceiling, bypassing spec §7.2's max_attempts.
        """
        rid = _insert_audit_row()
        self._rids.append(rid)

        settings = _settings()
        sink = worm_replicator.StubSink()
        sink.raise_on_call = True

        # First session — one failed attempt
        session1 = await _async_session()
        try:
            await worm_replicator.process_once(
                session1, settings=settings, sink=sink,
            )
        finally:
            await session1.close()

        row_after_first = _attempt_row(rid)
        assert row_after_first is not None
        assert row_after_first["attempt_count"] == 1

        # Clear the backoff window then open a *new* session to mimic
        # a restarted process.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE worm_replication_attempts "
                "SET next_attempt_at = NULL "
                "WHERE audit_record_id = CAST(:id AS uuid)"
            ).bindparams(id=rid))
            conn.commit()
        engine.dispose()

        session2 = await _async_session()
        try:
            await worm_replicator.process_once(
                session2, settings=settings, sink=sink,
            )
        finally:
            await session2.close()

        row_after_second = _attempt_row(rid)
        assert row_after_second is not None
        # Attempt count advanced from 1 → 2, not reset to 1 again.
        assert row_after_second["attempt_count"] == 2


def test_s3_key_for_is_deterministic() -> None:
    """Same audit row → same S3 key across runs."""

    class _Stub:
        id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        sequence_number = 42
        timestamp = datetime(2026, 4, 13, 12, tzinfo=UTC)

    first = worm_replicator._s3_key_for(_Stub())  # type: ignore[arg-type]
    second = worm_replicator._s3_key_for(_Stub())  # type: ignore[arg-type]
    assert first == second
    assert "2026" in first
    assert "04" in first
    assert "13" in first
    assert str(_Stub.id) in first


def test_audit_row_to_bytes_is_deterministic() -> None:
    """Canonical serialization is stable (required for integrity checks)."""
    from forge.models.enums import ActorType

    class _Stub:
        id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        sequence_number = 7
        event_id = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        actor_type = ActorType.system
        user_id = None
        user_role = None
        timestamp = datetime(2026, 4, 13, tzinfo=UTC)
        action_type = "test.action"
        entity_type = "test"
        entity_id = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        before_state = None
        after_state = None
        ip_address = None
        session_id = None
        previous_hash = "0" * 64
        record_hash = "1" * 64

    one = worm_replicator._audit_row_to_bytes(_Stub())  # type: ignore[arg-type]
    two = worm_replicator._audit_row_to_bytes(_Stub())  # type: ignore[arg-type]
    assert one == two
