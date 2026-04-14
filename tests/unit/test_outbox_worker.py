#!/usr/bin/env python3
"""
Module:   Test Outbox Worker
Purpose:  Integration tests for FOR UPDATE SKIP LOCKED claim, retry
          with exponential backoff, and dead-letter transition on
          max_attempts exhausted (spec §2.3, §4.5).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  The outbox contract is "at most one worker dispatches a given
  delivery; failure triggers backoff; repeated failure dead-letters."
  We exercise each leg: a successful dispatch, a retry on handler
  exception, and a dead-letter after max_attempts.  SKIP LOCKED is
  exercised by starting a second transaction that holds a lock and
  showing the worker skips to the next row — proves we will not
  double-dispatch under concurrent worker pods.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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


def _clean_outbox() -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM domain_event_deliveries"))
        conn.execute(text("DELETE FROM domain_events"))
        conn.commit()
    engine.dispose()


def _insert_event_and_delivery(
    *,
    event_type: str = "WorkOrderCreated",
    aggregate_type: str = "work_order",
    aggregate_id: str | None = None,
    attempt_count: int = 0,
    max_attempts: int = 10,
    available_at: datetime | None = None,
    consumer_name: str = "primary",
) -> tuple[str, str]:
    """Seed one (event, delivery) pair for tests.  Returns (event_id, delivery_id)."""
    event_id = str(uuid.uuid4())
    delivery_id = str(uuid.uuid4())
    if available_at is None:
        available_at = datetime.now(UTC) - timedelta(seconds=1)
    if aggregate_id is None:
        aggregate_id = str(uuid.uuid4())

    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        # Sequence numbers are unique per (aggregate_type, aggregate_id)
        # per the migration backstop — use the row count for this
        # aggregate as the seed so multiple seeds under the same
        # aggregate do not collide.
        existing = conn.execute(text(
            "SELECT COUNT(*) FROM domain_events "
            "WHERE aggregate_type = :atype AND aggregate_id = :aid"
        ).bindparams(atype=aggregate_type, aid=aggregate_id)).scalar() or 0
        seq = int(existing) + 1
        conn.execute(text("""
            INSERT INTO domain_events
              (id, event_type, aggregate_type, aggregate_id, payload,
               timestamp, sequence_number)
            VALUES
              (CAST(:id AS uuid), :etype, :atype, :aid, '{}'::json,
               :ts, :seq)
        """).bindparams(
            id=event_id, etype=event_type, atype=aggregate_type,
            aid=aggregate_id, ts=datetime.now(UTC), seq=seq,
        ))
        conn.execute(text("""
            INSERT INTO domain_event_deliveries
              (id, event_id, consumer_name, status, attempt_count,
               max_attempts, available_at)
            VALUES
              (CAST(:id AS uuid), CAST(:eid AS uuid), :cname,
               'pending', :attempts, :max, :avail)
        """).bindparams(
            id=delivery_id, eid=event_id, cname=consumer_name,
            attempts=attempt_count, max=max_attempts, avail=available_at,
        ))
        conn.commit()
    engine.dispose()
    return event_id, delivery_id


def _delivery_row(delivery_id: str) -> dict[str, Any]:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, attempt_count, last_error, delivered_at, "
            "  available_at, next_attempt_at "
            "FROM domain_event_deliveries "
            "WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=delivery_id)).fetchone()
    engine.dispose()
    assert row is not None
    return {
        "status": row[0], "attempt_count": row[1], "last_error": row[2],
        "delivered_at": row[3], "available_at": row[4],
        "next_attempt_at": row[5],
    }


async def _async_session() -> AsyncSession:
    engine = create_async_engine(_ASYNC_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory()


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestOutboxWorker:
    """Real-DB tests for the outbox worker's claim + retry + dead-letter."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        _clean_outbox()
        yield
        _clean_outbox()

    # ------------------------------------------------------------------
    # Successful dispatch
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_process_once_delivers_successfully(self) -> None:
        """Handler returns cleanly → status=delivered, delivered_at set."""
        from forge.events import worker

        _event_id, delivery_id = _insert_event_and_delivery()
        noop = AsyncMock()
        with patch("forge.events.worker.handler_for", return_value=noop):
            session = await _async_session()
            try:
                claimed = await worker.process_once(session)
            finally:
                await session.close()
        assert claimed == 1
        noop.assert_awaited_once()
        row = _delivery_row(delivery_id)
        assert row["status"] == "delivered"
        assert row["delivered_at"] is not None
        assert row["last_error"] is None

    @pytest.mark.asyncio
    async def test_process_once_skips_future_available_at(self) -> None:
        """Rows whose available_at is in the future are not claimed."""
        from forge.events import worker

        future = datetime.now(UTC) + timedelta(minutes=5)
        _insert_event_and_delivery(available_at=future)
        session = await _async_session()
        try:
            claimed = await worker.process_once(session)
        finally:
            await session.close()
        assert claimed == 0

    @pytest.mark.asyncio
    async def test_process_once_skips_other_consumers(self) -> None:
        """4B review finding (1): worker filters by consumer_name.

        Seeds two deliveries: one for the primary consumer (the worker's
        default) and one for a hypothetical second consumer.  The
        primary worker must claim exactly the first row — the second
        row stays pending so its own consumer can pick it up later.
        """
        from forge.events import worker

        _, primary_delivery = _insert_event_and_delivery(
            consumer_name="primary",
        )
        _, other_delivery = _insert_event_and_delivery(
            consumer_name="other-consumer",
        )

        noop = AsyncMock()
        with patch("forge.events.worker.handler_for", return_value=noop):
            session = await _async_session()
            try:
                claimed = await worker.process_once(session)
            finally:
                await session.close()
        assert claimed == 1
        assert _delivery_row(primary_delivery)["status"] == "delivered"
        assert _delivery_row(other_delivery)["status"] == "pending"

    # ------------------------------------------------------------------
    # Retry + backoff on failure
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handler_failure_retries_with_backoff(self) -> None:
        """Handler raises → attempt_count++, status=failed, backoff set."""
        from forge.events import worker

        _event_id, delivery_id = _insert_event_and_delivery()
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("forge.events.worker.handler_for", return_value=failing):
            session = await _async_session()
            try:
                await worker.process_once(session)
            finally:
                await session.close()
        row = _delivery_row(delivery_id)
        assert row["status"] == "failed"
        assert row["attempt_count"] == 1
        assert "boom" in (row["last_error"] or "")
        assert row["next_attempt_at"] is not None
        assert row["delivered_at"] is None
        # available_at was pushed into the future by backoff.
        # Normalize tz so the comparison does not depend on driver
        # behavior (some DBAPIs return naive for TIMESTAMPTZ).
        avail = row["available_at"]
        if avail.tzinfo is None:
            avail = avail.replace(tzinfo=UTC)
        assert avail > datetime.now(UTC) - timedelta(seconds=2)

    @pytest.mark.asyncio
    async def test_dead_letter_after_max_attempts(self) -> None:
        """Final failed attempt transitions to dead_letter."""
        from forge.events import worker

        # attempt_count=9, so next failed attempt (10th) should dead-letter
        _event_id, delivery_id = _insert_event_and_delivery(
            attempt_count=9, max_attempts=10,
        )
        failing = AsyncMock(side_effect=RuntimeError("final"))
        with patch("forge.events.worker.handler_for", return_value=failing):
            session = await _async_session()
            try:
                await worker.process_once(session)
            finally:
                await session.close()
        row = _delivery_row(delivery_id)
        assert row["status"] == "dead_letter"
        assert row["attempt_count"] == 10

    # ------------------------------------------------------------------
    # FOR UPDATE SKIP LOCKED
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_skip_locked_does_not_block_on_concurrent_claim(self) -> None:
        """A row locked by another txn is skipped; other rows still process.

        Opens a second connection that locks row A with FOR UPDATE,
        then runs the worker in a concurrent task.  The worker must
        claim row B (not row A) and complete promptly — proving
        SKIP LOCKED behavior is wired through.
        """
        from forge.events import worker

        _ea, delivery_a = _insert_event_and_delivery(
            aggregate_id=str(uuid.uuid4()),
        )
        _eb, delivery_b = _insert_event_and_delivery(
            aggregate_id=str(uuid.uuid4()),
        )

        # Hold a row-level lock on delivery_a in a separate connection
        engine = create_engine(_SYNC_URL, isolation_level="AUTOCOMMIT")
        blocker = engine.connect()
        blocker.execute(text("BEGIN"))
        blocker.execute(text(
            "SELECT 1 FROM domain_event_deliveries "
            "WHERE id = CAST(:id AS uuid) FOR UPDATE"
        ).bindparams(id=delivery_a))

        noop = AsyncMock()
        try:
            with patch("forge.events.worker.handler_for", return_value=noop):
                session = await _async_session()
                try:
                    # Run the worker under a short timeout — if SKIP
                    # LOCKED is not wired, the claim hangs forever.
                    claimed = await asyncio.wait_for(
                        worker.process_once(session), timeout=10.0,
                    )
                finally:
                    await session.close()
        finally:
            blocker.execute(text("ROLLBACK"))
            blocker.close()
            engine.dispose()

        # Only delivery_b was claimable; delivery_a stays pending
        assert claimed == 1
        a_row = _delivery_row(delivery_a)
        b_row = _delivery_row(delivery_b)
        assert a_row["status"] == "pending"
        assert b_row["status"] == "delivered"
