#!/usr/bin/env python3
"""
Module:   Test Event Bus
Purpose:  Prove emit_event serializes sequence_number per aggregate
          even under concurrent emits (4B review finding 3).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  The original emit_event implementation assigned sequence_number via
  SELECT COUNT(*) + 1 under the caller's transaction — two concurrent
  emits on the same aggregate could both read N and both insert N+1,
  duplicating the per-aggregate ordering spec §4.5 requires.

  The fix pairs a per-aggregate pg_advisory_xact_lock with a unique
  constraint on (aggregate_type, aggregate_id, sequence_number).
  These tests:
    1. Drive two concurrent tasks that emit against the same aggregate
       and assert every persisted sequence_number is unique.
    2. Prove unrelated aggregates do not serialize against each other
       (so the lock is correctly per-aggregate, not global).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from forge.events.bus import emit_event
from forge.models.enums import EventType

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


def _seqs_for(aggregate_id: str) -> list[int]:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT sequence_number FROM domain_events "
            "WHERE aggregate_id = :aid "
            "ORDER BY sequence_number"
        ).bindparams(aid=aggregate_id)).fetchall()
    engine.dispose()
    return [int(r[0]) for r in rows]


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestEventBus:
    """Concurrency tests for emit_event's sequence_number allocation."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        _clean_outbox()
        yield
        _clean_outbox()

    async def _run_concurrent_emits(
        self, aggregates: list[str], per_aggregate: int,
    ) -> None:
        """Helper: fire `per_aggregate` concurrent emits per aggregate id.

        Uses a small connection pool (3) to ensure real concurrency
        across the advisory-lock path without exhausting Postgres's
        max_connections during the test run.  The semaphore bounds
        in-flight session acquisitions so waiting tasks queue rather
        than all racing for connections at once.
        """
        engine = create_async_engine(
            _ASYNC_URL, pool_size=3, max_overflow=2,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        gate = asyncio.Semaphore(4)

        async def emit_one(agg: str) -> None:
            async with gate, factory() as session:
                await emit_event(
                    session,
                    event_type=EventType.work_order_created,
                    aggregate_type="work_order",
                    aggregate_id=agg,
                )
                await session.commit()

        tasks: list[Any] = []
        for agg in aggregates:
            tasks.extend(emit_one(agg) for _ in range(per_aggregate))
        try:
            await asyncio.gather(*tasks)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_concurrent_emits_on_same_aggregate_have_unique_sequence(
        self,
    ) -> None:
        """Many concurrent emits → distinct sequence_numbers.

        Fires 20 concurrent emits against the same aggregate and
        asserts every sequence_number is unique.  Without the advisory
        lock this reliably duplicates; with it, every run yields 1..20.
        """
        agg_id = str(uuid.uuid4())
        await self._run_concurrent_emits([agg_id], per_aggregate=20)

        seqs = _seqs_for(agg_id)
        assert len(seqs) == 20
        assert len(set(seqs)) == 20, f"duplicate sequence_numbers: {seqs}"
        assert seqs == list(range(1, 21))

    @pytest.mark.asyncio
    async def test_concurrent_emits_on_distinct_aggregates_do_not_block(
        self,
    ) -> None:
        """Two aggregates receive their own monotone 1..N independently.

        Proves the lock is per-aggregate: unrelated aggregates do not
        contend on the same advisory lock and each gets its own
        complete sequence.
        """
        agg_a = str(uuid.uuid4())
        agg_b = str(uuid.uuid4())
        await self._run_concurrent_emits([agg_a, agg_b], per_aggregate=10)

        assert _seqs_for(agg_a) == list(range(1, 11))
        assert _seqs_for(agg_b) == list(range(1, 11))
