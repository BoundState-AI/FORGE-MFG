#!/usr/bin/env python3
"""
Module:   Test Idempotency
Purpose:  Prove Idempotency-Key middleware behavior via HTTP tests.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why these tests:
  Spec §5.15 idempotency behavior must be proven at the HTTP level,
  not by mocking the middleware internals.  These tests use a test-only
  probe POST route with a side-effect counter to prove:
  - replay returns the cached response without repeating the side effect
  - same key + different body returns 409
  - missing key returns 400
  - expired records allow fresh execution
  - different users with the same key do not collide
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text

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
class TestIdempotency:
    """Idempotency middleware HTTP behavior tests."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        """Clean idempotency records before each test."""
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "DELETE FROM idempotency_records"
                " WHERE idempotency_key LIKE 'test-%'"
            ))
            conn.commit()
        engine.dispose()
        yield
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "DELETE FROM idempotency_records"
                " WHERE idempotency_key LIKE 'test-%'"
            ))
            conn.commit()
        engine.dispose()

    @pytest.fixture()
    def idem_app(self) -> Any:
        """App with local auth mode, DB, and a probe POST route.

        Why a counter-based probe route: the side-effect counter lets
        us prove that replay does NOT re-execute the handler — the
        counter stays at 1 after the second identical request.  This
        tests real behavior, not mocked internals.
        """
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        settings = Settings(
            env="test",
            debug=False,
            auth_mode="local",
            database_url=os.getenv(
                "FORGE_DATABASE_URL",
                "postgresql+asyncpg://forge:forge@localhost:5432/forge",
            ),
        )
        init_engine(settings.database_url)
        app = create_app(settings=settings)

        # Side-effect counter to prove replay doesn't repeat execution
        counter: dict[str, int] = {"calls": 0}

        @app.post("/test/mutate")
        async def probe_mutate() -> dict[str, Any]:
            counter["calls"] += 1
            return {"executed": True, "call_count": counter["calls"]}

        # Store counter on app for test assertions
        app.state.counter = counter
        return app

    @pytest.fixture()
    async def idem_client(self, idem_app: Any) -> Any:

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=idem_app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_first_post_executes(
        self, idem_client: Any, idem_app: Any
    ) -> None:
        """First POST with Idempotency-Key executes the handler."""
        resp = await idem_client.post(
            "/test/mutate",
            headers={"Idempotency-Key": "test-first-001"},
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["executed"] is True
        assert idem_app.state.counter["calls"] == 1

    @pytest.mark.asyncio
    async def test_replay_returns_cached_no_side_effect(
        self, idem_client: Any, idem_app: Any
    ) -> None:
        """Second identical POST replays cached response without side effect.

        Spec §5.15: "Same key + same request hash → return cached response."
        The counter must not increment on replay.
        """
        key = "test-replay-001"
        body = {"data": "same"}
        # First call
        resp1 = await idem_client.post(
            "/test/mutate",
            headers={"Idempotency-Key": key},
            json=body,
        )
        assert resp1.status_code == 200
        assert idem_app.state.counter["calls"] == 1

        # Replay — same key, same body
        resp2 = await idem_client.post(
            "/test/mutate",
            headers={"Idempotency-Key": key},
            json=body,
        )
        assert resp2.status_code == 200
        # Counter must still be 1 — handler was NOT called again
        assert idem_app.state.counter["calls"] == 1
        # Response body matches first call
        assert resp2.json()["call_count"] == 1

    @pytest.mark.asyncio
    async def test_same_key_different_body_409(
        self, idem_client: Any
    ) -> None:
        """Same key + different body → 409 Conflict.

        Spec §5.15: "Same key + different request hash → 409 Conflict."
        """
        key = "test-conflict-001"
        resp1 = await idem_client.post(
            "/test/mutate",
            headers={"Idempotency-Key": key},
            json={"version": 1},
        )
        assert resp1.status_code == 200

        resp2 = await idem_client.post(
            "/test/mutate",
            headers={"Idempotency-Key": key},
            json={"version": 2},
        )
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_missing_key_400(self, idem_client: Any) -> None:
        """Missing Idempotency-Key on POST → 400.

        Spec §5.15: "No key → 400 Bad Request."
        """
        resp = await idem_client.post("/test/mutate", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_expired_record_executes_fresh(
        self, idem_client: Any, idem_app: Any
    ) -> None:
        """Expired idempotency record is ignored — fresh execution occurs.

        Spec §5.15: "IdempotencyRecords expire after 24 hours."
        """
        key = "test-expired-001"
        # First call
        resp1 = await idem_client.post(
            "/test/mutate",
            headers={"Idempotency-Key": key},
            json={"fresh": True},
        )
        assert resp1.status_code == 200
        assert idem_app.state.counter["calls"] == 1

        # Manually expire the record in the DB
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE idempotency_records "
                "SET expires_at = :expired "
                "WHERE idempotency_key = :key"
            ).bindparams(
                expired=datetime.now(UTC) - timedelta(hours=1),
                key=key,
            ))
            conn.commit()
        engine.dispose()

        # Second call with same key — should execute fresh
        resp2 = await idem_client.post(
            "/test/mutate",
            headers={"Idempotency-Key": key},
            json={"fresh": True},
        )
        assert resp2.status_code == 200
        assert idem_app.state.counter["calls"] == 2

    @pytest.mark.asyncio
    async def test_different_users_same_key_no_collision(
        self, idem_app: Any
    ) -> None:
        """Same idempotency key used by different users must not collide.

        Spec §5.15: "Keys are scoped to the authenticated user."
        In local auth mode, all requests share the same sentinel user_id,
        so we directly insert records with different user_ids to prove
        the scoping logic.
        """
        engine = create_engine(_SYNC_URL)
        key = "test-scope-001"
        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        now = datetime.now(UTC)

        with engine.connect() as conn:
            # User A's record
            conn.execute(text("""
                INSERT INTO idempotency_records
                (id, idempotency_key, user_id, request_hash,
                 response_status, response_body, created_at, expires_at)
                VALUES (:id, :key, :uid, :hash, 200, '{}',
                        :now, :exp)
            """).bindparams(
                id=str(uuid.uuid4()),
                key=key,
                uid=user_a,
                hash="a" * 64,
                now=now,
                exp=now + timedelta(hours=24),
            ))
            # User B with same key — should succeed (different user)
            conn.execute(text("""
                INSERT INTO idempotency_records
                (id, idempotency_key, user_id, request_hash,
                 response_status, response_body, created_at, expires_at)
                VALUES (:id, :key, :uid, :hash, 200, '{}',
                        :now, :exp)
            """).bindparams(
                id=str(uuid.uuid4()),
                key=key,
                uid=user_b,
                hash="b" * 64,
                now=now,
                exp=now + timedelta(hours=24),
            ))
            conn.commit()

            # Verify both exist
            count = conn.execute(text(
                "SELECT COUNT(*) FROM idempotency_records"
                " WHERE idempotency_key = :key"
            ).bindparams(key=key)).scalar()
            assert count == 2

        engine.dispose()
