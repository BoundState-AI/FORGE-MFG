#!/usr/bin/env python3
"""
Module:   Test Work Order Lifecycle
Purpose:  Integration tests for the 2A done-means checklist gates.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why these tests:
  Checklist 2A done-means requires proving work order creation, operation
  addition, release, execution, labor, version checks, and rate limiting
  through real route+service+DB flow — not service-only shortcuts.
  These tests run against the actual FastAPI app with local auth mode
  and a live Postgres database.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")

_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


OTHER_USER_ID = "00000000-0000-0000-0000-000000000099"


def _ensure_other_user() -> str:
    """Ensure a second test user exists for SOD-004 tests."""
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=OTHER_USER_ID)).fetchone()
        if not row:
            conn.execute(text("""
                INSERT INTO users (id, cognito_sub, email, display_name,
                    is_system_actor, active, created_by, updated_by)
                VALUES (CAST(:id AS uuid), 'test-other', 'other@test.com',
                    'Other User', false, true,
                    CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=OTHER_USER_ID, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return OTHER_USER_ID


def _ensure_test_program() -> str:
    """Ensure a test program exists and return its ID."""
    engine = create_engine(_SYNC_URL)
    prog_id = str(uuid.uuid4())
    with engine.connect() as conn:
        # Check if any program exists
        row = conn.execute(text("SELECT id FROM programs LIMIT 1")).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), 'TST-2A', 'Test Program 2A',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestWorkOrderLifecycle:
    """Integration tests proving the full 2A lifecycle through HTTP routes."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        """Set up test app with local auth and clean DB state."""
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        # Clean before each test to avoid stale data from prior runs
        self._clean_test_data()
        self.prog_id = _ensure_test_program()
        self.other_user_id = _ensure_other_user()

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        yield
        # Also clean after to leave DB tidy
        self._clean_test_data()

    def _clean_test_data(self) -> None:
        """Remove all test-created data using sync engine.

        Why try/finally: the trigger must always be re-enabled even
        if a DELETE fails, to avoid leaving the DB in an unprotected
        state for subsequent tests.
        """
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text(
                    "DELETE FROM audit_records "
                    "WHERE action_type LIKE 'work_order.%' "
                    "   OR action_type LIKE 'operation.%'"
                ))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text("DELETE FROM work_orders"))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def _other_user_client(self) -> Any:
        """Client with a DIFFERENT user identity for SOD-004 tests.

        Sets a temporary auth override on the existing app via
        app.state.auth_override so the auth middleware uses a
        different sub than the default local dev user.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms

        self.app.state.auth_override = AuthContext(
            sub=OTHER_USER_ID,
            username="other-user",
            roles=["engineer"],
            programs=["*"],
            permissions=frozenset(all_perms),
        )

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        client = AsyncClient(transport=transport, base_url="http://test")

        # The caller must clean up auth_override after use
        return client

    async def _create_wo(self, client: Any) -> dict[str, Any]:
        """Helper: create a work order and return response JSON."""
        resp = await client.post(
            "/api/work-orders",
            json={
                "program_id": self.prog_id,
                "engine_serial": "E-2A-001",
                "module": "Powerhead",
                "build_phase": "fabrication",
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert resp.status_code == 201, resp.text
        result: dict[str, Any] = resp.json()
        return result

    async def _release_wo(self, wo: dict[str, Any]) -> dict[str, Any]:
        """Release a WO using a different user (SOD-004 compliance)."""
        async with await self._other_user_client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        # Clean up the override so subsequent calls use normal auth
        self.app.state.auth_override = None
        assert resp.status_code == 200, resp.text
        result: dict[str, Any] = resp.json()
        return result

    async def _add_op(
        self, client: Any, wo_id: str, seq: int = 1
    ) -> dict[str, Any]:
        """Helper: add an operation to a WO."""
        resp = await client.post(
            f"/api/work-orders/{wo_id}/operations",
            json={
                "description": f"Op {seq}",
                "sequence": seq,
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert resp.status_code == 201, resp.text
        result: dict[str, Any] = resp.json()
        return result

    @pytest.mark.asyncio
    async def test_create_work_order(self) -> None:
        """Checklist: integration test creates a work order."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
        assert wo["status"] == "draft"
        assert wo["wo_number"].startswith("WO-")

    @pytest.mark.asyncio
    async def test_add_operations(self) -> None:
        """Checklist: integration test adds operations."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
            op1 = await self._add_op(client, wo["id"], seq=1)
            op2 = await self._add_op(client, wo["id"], seq=2)
        assert op1["sequence"] == 1
        assert op2["sequence"] == 2

    @pytest.mark.asyncio
    async def test_release_work_order(self) -> None:
        """Checklist: work order release works (different user per SOD-004)."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
        released = await self._release_wo(wo)
        assert released["status"] == "released"

    @pytest.mark.asyncio
    async def test_operation_execution(self) -> None:
        """Checklist: operation execution works (start, stop, complete)."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
            op = await self._add_op(client, wo["id"])

        # Release WO with different user (SOD-004)
        await self._release_wo(wo)

        async with await self._client() as client:
            # Start operation
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            started = resp.json()
            assert started["status"] == "in_progress"

            # Stop labor
            resp = await client.post(
                f"/api/operations/{op['id']}/stop",
                json={"version": started["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200

            # Complete operation
            stopped = resp.json()
            resp = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": stopped["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "complete"

    @pytest.mark.asyncio
    async def test_labor_ties_user_operation(self) -> None:
        """Checklist: labor ties user + operation correctly."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
            op = await self._add_op(client, wo["id"])

        # Release with different user (SOD-004)
        await self._release_wo(wo)

        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200

        # Verify labor entry exists in DB
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT operation_id, role FROM labor_entries LIMIT 1"
            )).fetchone()
        engine.dispose()
        assert row is not None
        assert str(row[0]) == op["id"]
        assert row[1] == "operator"

    @pytest.mark.asyncio
    async def test_version_check_enforced(self) -> None:
        """Checklist: version checks enforced on mutating endpoints."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
            # Try to update with stale version
            resp = await client.patch(
                f"/api/work-orders/{wo['id']}",
                json={"version": 999, "module": "TPA"},
            )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_draft_only_editing(self) -> None:
        """Do-not-advance: editing a released WO must fail."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
        # Release with different user (SOD-004)
        released = await self._release_wo(wo)
        async with await self._client() as client:
            # Try to edit — should fail because WO is released
            resp = await client.patch(
                f"/api/work-orders/{wo['id']}",
                json={"version": released["version"], "module": "TPA"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_illegal_transition_fails(self) -> None:
        """Do-not-advance: illegal transition returns 422."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
            # Try draft → complete (illegal)
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/complete",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rework_increments_execution_cycle(self) -> None:
        """Spec §3.1: rework → in_progress increments execution_cycle."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
            op = await self._add_op(client, wo["id"])

        # Release with different user (SOD-004)
        await self._release_wo(wo)

        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            started = resp.json()
            assert started["execution_cycle"] == 1

            # Rework
            resp = await client.post(
                f"/api/operations/{op['id']}/rework",
                json={"version": started["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            reworked = resp.json()
            assert reworked["status"] == "rework"
            assert reworked["execution_cycle"] == 1  # not yet incremented

            # Resume from rework via start (rework → in_progress)
            # This should increment execution_cycle to 2
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": reworked["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            resumed = resp.json()
            assert resumed["status"] == "in_progress"
            assert resumed["execution_cycle"] == 2

    @pytest.mark.asyncio
    async def test_rate_limit_smoke(self) -> None:
        """Checklist: rate-limit smoke test on a real mutating endpoint.

        Enables rate limiting on the test app and fires enough requests
        to exceed the 120/min default limit for general endpoints.
        Verifies at least one 429 response with Retry-After header.
        """
        from forge.api.middleware import rate_limit as rl

        # Enable rate limiting for this test
        self.app.state.enable_rate_limit = True
        rl._request_log.clear()

        # Tighten the limit and extend the window for deterministic tests.
        # This avoids false negatives if the loop takes >60s on a slow DB.
        old_limit = rl.DEFAULT_LIMIT
        old_window = rl.WINDOW_SECONDS
        rl.DEFAULT_LIMIT = 10
        rl.WINDOW_SECONDS = 3600

        try:
            async with await self._client() as client:
                wo = await self._create_wo(client)
                last_version = wo["version"]
                got_429 = False

                # Fire requests until we hit the limit
                for i in range(rl.DEFAULT_LIMIT + 5):
                    resp = await client.patch(
                        f"/api/work-orders/{wo['id']}",
                        json={"version": last_version, "module": f"Mod-{i}"},
                    )
                    if resp.status_code == 429:
                        got_429 = True
                        assert "Retry-After" in resp.headers
                        break
                    if resp.status_code == 200:
                        last_version = resp.json()["version"]

                assert got_429, "Rate limiter did not trigger 429"
        finally:
            rl.DEFAULT_LIMIT = old_limit
            rl.WINDOW_SECONDS = old_window
            self.app.state.enable_rate_limit = False
            rl._request_log.clear()
        self.app.state.enable_rate_limit = False

    @pytest.mark.asyncio
    async def test_delete_operation_version_check(self) -> None:
        """Version check enforced on DELETE /api/operations/{id}."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
            op = await self._add_op(client, wo["id"])

            # Stale version → 409
            resp = await client.delete(
                f"/api/operations/{op['id']}?version=999",
            )
            assert resp.status_code == 409

            # Correct version → 204
            resp = await client.delete(
                f"/api/operations/{op['id']}?version={op['version']}",
            )
            assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_cross_program_access_denied(self) -> None:
        """Program-scope: user with program B cannot access WO in program A.

        Uses a custom app that sets auth.programs to a different program
        than the one the WO belongs to.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        # First create a WO using the normal (all-access) app
        async with await self._client() as client:
            wo = await self._create_wo(client)

        # Set auth override to a user with a different program
        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms

        self.app.state.auth_override = AuthContext(
            sub=OTHER_USER_ID,
            username="restricted-user",
            roles=["engineer"],
            programs=["OTHER-PROGRAM"],  # NOT the test program
            permissions=frozenset(all_perms),
        )

        async with await self._client() as client:
            # Try to get the WO — should be 403
            resp = await client.get(f"/api/work-orders/{wo['id']}")
            assert resp.status_code == 403

            # Try to release — should also be 403
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 403

        self.app.state.auth_override = None

    @pytest.mark.asyncio
    async def test_sod004_author_cannot_release(self) -> None:
        """SOD-004: the WO author cannot release their own work order.

        Spec §4.2: "Work order release independence — author cannot
        release the same WO."  Spec §5.1 line 885: release requires
        SOD-004 check.
        """
        async with await self._client() as client:
            wo = await self._create_wo(client)
            # The local dev user is also the authored_by_id.
            # Release should fail with SOD-004 violation.
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 422
        body = resp.json()
        assert "SOD-004" in str(body)

    @pytest.mark.asyncio
    async def test_sod004_non_author_can_release(self) -> None:
        """SOD-004 success path: a different user can release the WO."""
        async with await self._client() as client:
            wo = await self._create_wo(client)
        released = await self._release_wo(wo)
        assert released["status"] == "released"

    @pytest.mark.asyncio
    async def test_cross_program_labor_update_denied(self) -> None:
        """Program-scope: user with program B cannot PATCH labor in program A.

        Creates a WO/op/labor in the test program, then switches to a
        user whose auth context has a different program.  The labor
        update must return 403 via the service-layer scope check.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        # Create WO + op + labor in the test program (program A)
        async with await self._client() as client:
            wo = await self._create_wo(client)
            op = await self._add_op(client, wo["id"])

        # Release with different user (SOD-004) so we can start the op
        await self._release_wo(wo)

        # Start the op to create a labor entry
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200

        # Find the labor entry ID
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, version FROM labor_entries LIMIT 1"
            )).fetchone()
        engine.dispose()
        assert row is not None
        labor_id = str(row[0])
        labor_version = row[1]

        # Switch to a user with a DIFFERENT program scope
        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms

        self.app.state.auth_override = AuthContext(
            sub=OTHER_USER_ID,
            username="other-program-user",
            roles=["engineer"],
            programs=["OTHER-PROGRAM"],  # NOT the test program
            permissions=frozenset(all_perms),
        )

        async with await self._client() as client:
            resp = await client.patch(
                f"/api/labor/{labor_id}",
                json={"version": labor_version, "notes": "hack"},
            )

        self.app.state.auth_override = None
        assert resp.status_code == 403
