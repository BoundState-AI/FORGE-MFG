#!/usr/bin/env python3
"""
Module:   Test Labor Control (Force-Stop / Stop-Request / Flag-Correction)
Purpose:  Integration coverage for UX spec v1.5.1 §5.4.9 + §5.4.5 +
          §5.3 Hold Cascading (slice 5B-12 + 5B-13).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The labor-control contract is the safety-critical "stop the
  timer" surface for the floor.  Coverage here pins:
    - force-stop happy path + own-entry / already-stopped /
      stale-version rejection (LABOR-STOP-001/002 + 409)
    - stop-request happy path + duplicate pending rejection
      (LABOR-STOP-REQ-002)
    - flag-correction happy path + own-entry rejection
    - role enforcement: qa_manager cannot force-stop even though
      they hold labor:update_any (spec-explicit LABOR-STOP-003)
    - operation detail serves real pending stop-requests and
      OMITS fulfilled/expired ones
    - WO hold with force_stop_labor=True cascades and emits
      labor.stopped_by_hold; pending stop-requests expire
    - WO hold without force_stop_labor leaves active timers alone
    - canonical /api/labor-entries/* surface is real
    - DB role-seed alignment for the three new labor-control perms
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
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
# Slice `ad`..`af` + `b2`/`b3` — avoids every prior suite's UUID slot.
AUTHOR_USER_ID = "00000000-0000-0000-0000-0000000000ad"
ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000ae"
OPERATOR_ONE_ID = "00000000-0000-0000-0000-0000000000af"
OPERATOR_TWO_ID = "00000000-0000-0000-0000-0000000000b2"
QA_MANAGER_ID = "00000000-0000-0000-0000-0000000000b3"

PROG_A_CODE = "TST-LABOR-A"
PROG_B_CODE = "TST-LABOR-B"


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _ensure_user(uid: str, email: str, name: str) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=uid)).fetchone()
        if not row:
            conn.execute(text("""
                INSERT INTO users (id, cognito_sub, email, display_name,
                    is_system_actor, active, created_by, updated_by)
                VALUES (CAST(:id AS uuid), :sub, :email, :name,
                    false, true,
                    CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=uid, sub=f"lab-{uid[-4:]}",
                email=email, name=name, sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_program(code: str) -> str:
    engine = create_engine(_SYNC_URL)
    prog_id: str | None = None
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :code"
        ).bindparams(code=code)).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            prog_id = str(uuid.uuid4())
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name, true,
                    CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=code, name=f"Labor Control {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    assert prog_id is not None
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestLaborControl:
    """Integration coverage for labor-control + hold cascade."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Generator[None]:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_a_id = _ensure_program(PROG_A_CODE)
        self.prog_b_id = _ensure_program(PROG_B_CODE)
        _ensure_user(AUTHOR_USER_ID, "author-l@test.com", "Author Labor")
        _ensure_user(ENGINEER_USER_ID, "eng-l@test.com", "Engineer Labor")
        _ensure_user(OPERATOR_ONE_ID, "op1-l@test.com", "Operator One")
        _ensure_user(OPERATOR_TWO_ID, "op2-l@test.com", "Operator Two")
        _ensure_user(QA_MANAGER_ID, "qa-l@test.com", "QA Manager")

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        yield
        self._clean_test_data()

    def _clean_test_data(self) -> None:
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
                    "   OR action_type LIKE 'operation.%' "
                    "   OR action_type LIKE 'labor.%'"
                ))
                conn.execute(text("DELETE FROM stop_requests"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operation_predecessors"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text(
                    "DELETE FROM work_orders WHERE program_id IN ("
                    "  SELECT id FROM programs WHERE code IN (:a, :b)"
                    ")"
                ).bindparams(a=PROG_A_CODE, b=PROG_B_CODE))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    # ------------------------------------------------------------------
    # Auth + client helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, roles: list[str],
        programs: list[str] | None = None,
    ) -> None:
        """Pack only the listed roles' perms — negative tests rely on
        NOT getting union-of-all-roles."""
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        perms: set[str] = set()
        for role in roles:
            perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"l-{user_id[-4:]}",
            roles=roles, programs=programs or ["*"],
            permissions=frozenset(perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Build helpers — WO + released + labor-entry creation per user
    # ------------------------------------------------------------------

    def _assert_op_count(
        self, wo_id: str, expected: int, stage: str,
    ) -> None:
        """DB-level sanity check: WO has `expected` ops at this stage.

        Temporary instrumentation added during the 5B-12/13 flake
        investigation.  If setup/release is silently losing ops,
        this assert surfaces it at the origin rather than letting
        a downstream query report "no ops found" and look like a
        graph/force-stop bug.
        """
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) FROM operations "
                "WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo_id)).fetchone()
        engine.dispose()
        actual = int(row[0]) if row is not None else 0
        assert actual == expected, (
            f"[{stage}] op count mismatch on WO {wo_id}: "
            f"expected {expected}, got {actual}"
        )

    async def _make_wo_and_release(
        self, program_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a WO with one op, release it.  Returns the released WO."""
        self._override_as(AUTHOR_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": program_id or self.prog_a_id,
                    "engine_serial": f"E-L-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            wo = resp.json()
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={"description": "Op 1", "sequence": 1},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
        self._clear_override()
        self._assert_op_count(wo["id"], 1, stage="after add_op")

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        self._assert_op_count(wo["id"], 1, stage="after release")
        return resp.json()  # type: ignore[no-any-return]

    async def _start_op_as(
        self, op_id: str, user_id: str,
    ) -> dict[str, Any]:
        """Start the op as `user_id` — op + labor entry both attach."""
        # Load current op version first (releasing bumped it).
        self._override_as(user_id, roles=["operator"])
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{op_id}")
            assert resp.status_code == 200, resp.text
            op = resp.json()

            resp = await client.post(
                f"/api/operations/{op_id}/start",
                json={
                    "version": op["version"],
                    "role": "operator",
                    "shift": "day",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _add_labor_as(
        self, op_id: str, user_id: str,
    ) -> dict[str, Any]:
        """Create an additional labor entry on an op as `user_id`."""
        self._override_as(user_id, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op_id}/labor",
                json={"role": "operator", "shift": "day"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def _find_active_labor_for(
        self, op_id: str, user_id: str,
    ) -> dict[str, Any]:
        """DB-level lookup of an active labor entry for a user on an op."""
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, version FROM labor_entries "
                "WHERE operation_id = CAST(:o AS uuid) "
                "  AND user_id = CAST(:u AS uuid) "
                "  AND stopped_at IS NULL LIMIT 1"
            ).bindparams(o=op_id, u=user_id)).fetchone()
        engine.dispose()
        assert row is not None, (
            f"no active labor for {user_id} on op {op_id}"
        )
        return {"id": str(row[0]), "version": int(row[1])}

    # ------------------------------------------------------------------
    # DB seed alignment — regression guard
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_db_role_seed_has_new_labor_perms(self) -> None:
        """Engineer row gains labor:force_stop; operator row gains
        labor:request_stop + labor:flag_correction (migration
        f4d8b2e6c31a).  This is the live-DB drift guard that
        caught the 5B-7 inspect:read hole.
        """
        import json

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = {
                name: perms for name, perms in conn.execute(text(
                    "SELECT name, permissions FROM roles"
                )).all()
            }
        engine.dispose()

        def _as_set(raw: Any) -> set[str]:
            return set(raw if isinstance(raw, list) else json.loads(raw))

        engineer_perms = _as_set(rows["engineer"])
        operator_perms = _as_set(rows["operator"])
        qa_perms = _as_set(rows["qa_manager"])

        assert "labor:force_stop" in engineer_perms
        assert "labor:request_stop" in operator_perms
        assert "labor:flag_correction" in operator_perms
        # qa_manager must NOT silently pick these up.
        assert "labor:force_stop" not in qa_perms

    # ------------------------------------------------------------------
    # Force-stop (engineer)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_force_stop_happy_path(self) -> None:
        """Engineer force-stops an operator's labor entry.

        Asserts:
          - labor entry stopped_at is set
          - version bumps
          - labor.stopped_by_engineer audit event is recorded
        """
        wo = await self._make_wo_and_release()
        op = wo  # temp, rebind after reading ops
        async with await self._client() as client:
            resp = await client.get(f"/api/work-orders/{wo['id']}")
            ops = [o for o in resp.json().get("operations", [])]
            # WorkOrderOut doesn't embed ops; fetch list via operations
            # route via DB-level query to keep the test short
        # Simpler: list ops via DB.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        assert row is not None
        op_id = str(row[0])
        del op, ops

        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/force-stop",
                json={
                    "rationale": "Safety concern on weld profile",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stopped_at"] is not None
        assert body["version"] == labor["version"] + 1

        # Audit event name check.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT action_type FROM audit_records "
                "WHERE entity_type = 'labor_entry' "
                "  AND entity_id = CAST(:id AS uuid) "
                "  AND action_type = 'labor.stopped_by_engineer' "
                "LIMIT 1"
            ).bindparams(id=labor["id"])).fetchone()
        engine.dispose()
        assert row is not None, "labor.stopped_by_engineer audit missing"

    @pytest.mark.asyncio
    async def test_force_stop_own_entry_rejected(self) -> None:
        """LABOR-STOP-002: cannot force-stop own labor entry."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, ENGINEER_USER_ID)
        labor = await self._find_active_labor_for(op_id, ENGINEER_USER_ID)

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/force-stop",
                json={
                    "rationale": "this should not work on own entry",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 422, resp.text
        assert "LABOR-STOP-002" in str(resp.json())

    @pytest.mark.asyncio
    async def test_force_stop_already_stopped_rejected(self) -> None:
        """LABOR-STOP-001: already-stopped entry cannot be force-stopped."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        # First force-stop succeeds.
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/force-stop",
                json={
                    "rationale": "safety stop first time",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200
            new_version = resp.json()["version"]

            # Second attempt on the same stopped entry -> LABOR-STOP-001.
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/force-stop",
                json={
                    "rationale": "second attempt should fail",
                    "labor_entry_version": new_version,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 422, resp.text
        assert "LABOR-STOP-001" in str(resp.json())

    @pytest.mark.asyncio
    async def test_force_stop_stale_version_returns_409(self) -> None:
        """VERSION_CONFLICT on stale labor_entry_version."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/force-stop",
                json={
                    "rationale": "stale version test",
                    "labor_entry_version": labor["version"] - 1,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_force_stop_qa_manager_forbidden(self) -> None:
        """qa_manager has labor:update_any but NOT labor:force_stop.

        LABOR-STOP-003 path in disguise — the permission dep rejects
        qa_manager with 403 BEFORE hitting the service's body check,
        which is the correct defense-in-depth shape per §5.4.9.
        """
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(QA_MANAGER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/force-stop",
                json={
                    "rationale": "qa manager attempt should 403",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 403

    # ------------------------------------------------------------------
    # Stop-request (operator)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stop_request_happy_path(self) -> None:
        """Operator creates a pending stop-request on someone else's entry."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(OPERATOR_TWO_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/stop-request",
                json={
                    "note": "op1 looks confused on the setup",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["labor_entry_id"] == labor["id"]
        assert body["requested_by"]["user_id"] == OPERATOR_TWO_ID

    @pytest.mark.asyncio
    async def test_stop_request_duplicate_pending_rejected(self) -> None:
        """LABOR-STOP-REQ-002: only one pending per entry (DB partial-unique)."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(OPERATOR_TWO_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/stop-request",
                json={
                    "note": "first request",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201

            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/stop-request",
                json={
                    "note": "duplicate should fail",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 422, resp.text
        assert "LABOR-STOP-REQ-002" in str(resp.json())

    @pytest.mark.asyncio
    async def test_stop_request_own_entry_rejected(self) -> None:
        """LABOR-STOP-REQ-003: cannot request stop on own entry."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(OPERATOR_ONE_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/stop-request",
                json={
                    "note": "cannot self-request",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 422, resp.text
        assert "LABOR-STOP-REQ-003" in str(resp.json())

    # ------------------------------------------------------------------
    # Flag-correction (operator)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_flag_correction_happy_path_204(self) -> None:
        """POST /flag-correction returns 204 and emits audit."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(OPERATOR_TWO_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/flag-correction",
                json={"note": "op1 wrote the wrong hours"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 204, resp.text

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT action_type FROM audit_records "
                "WHERE entity_type = 'labor_entry' "
                "  AND entity_id = CAST(:id AS uuid) "
                "  AND action_type = 'labor.correction_requested' "
                "LIMIT 1"
            ).bindparams(id=labor["id"])).fetchone()
        engine.dispose()
        assert row is not None

    @pytest.mark.asyncio
    async def test_flag_correction_own_entry_rejected(self) -> None:
        """Cannot flag own entry — edit it via labor:update_own instead."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(OPERATOR_ONE_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/flag-correction",
                json={"note": "self flag should fail"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 422

    # ------------------------------------------------------------------
    # Operation-detail stop_requests read model
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_operation_detail_returns_pending_stop_requests(
        self,
    ) -> None:
        """GET /api/operations/{id} surfaces pending stop-requests.

        Also pins the NEGATIVE case: after force-stop fulfills the
        request, it disappears from operation detail (fulfilled rows
        are not in the default read).
        """
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        # Operator 2 requests stop.
        self._override_as(OPERATOR_TWO_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/stop-request",
                json={
                    "note": "concern noted",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201
            req_id = resp.json()["request_id"]
        self._clear_override()

        # Operation detail includes the pending request.
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.get(f"/api/operations/{op_id}")
        self._clear_override()
        assert resp.status_code == 200
        detail = resp.json()
        assert len(detail["stop_requests"]) == 1
        assert detail["stop_requests"][0]["request_id"] == req_id
        assert detail["stop_requests"][0]["status"] == "pending"

        # Engineer force-stops → request becomes fulfilled → disappears
        # from operation detail.
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor['id']}/force-stop",
                json={
                    "rationale": "acting on stop-request",
                    "labor_entry_version": labor["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200

            resp = await client.get(f"/api/operations/{op_id}")
        self._clear_override()
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["stop_requests"] == [], (
            f"fulfilled stop-request should disappear; got {detail['stop_requests']}"
        )

    # ------------------------------------------------------------------
    # WO hold cascade — 5B-13
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_hold_without_force_stop_leaves_labor_running(
        self,
    ) -> None:
        """Administrative hold (force_stop_labor=False) does not stop timers."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor_before = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        # Load WO current version for hold.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT version FROM work_orders WHERE id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        wo_version = int(row[0])

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={"version": wo_version, "force_stop_labor": False},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["force_stop_labor"] is False
        assert body["stopped_labor_entries"] == []

        # Labor entry is still active.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT stopped_at FROM labor_entries "
                "WHERE id = CAST(:id AS uuid)"
            ).bindparams(id=labor_before["id"])).fetchone()
        engine.dispose()
        assert row is not None
        assert row[0] is None

    @pytest.mark.asyncio
    async def test_hold_with_force_stop_cascades(self) -> None:
        """force_stop_labor=True stops all active labor, emits audit,
        and expires pending stop-requests."""
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])

        # Two operators start timers.
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        await self._add_labor_as(op_id, OPERATOR_TWO_ID)
        labor_1 = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        # Operator 2 requests stop on op1 (pending).
        self._override_as(OPERATOR_TWO_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/labor-entries/{labor_1['id']}/stop-request",
                json={
                    "note": "pending before hold",
                    "labor_entry_version": labor_1["version"],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201
            pending_id = resp.json()["request_id"]
        self._clear_override()

        # Load WO current version for hold.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT version FROM work_orders WHERE id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        wo_version = int(row[0])

        # Stop-work hold.
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/hold",
                json={
                    "version": wo_version,
                    "force_stop_labor": True,
                    "reason": "safety stand-down",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["force_stop_labor"] is True
        assert len(body["stopped_labor_entries"]) == 2
        stopped_ids = {s["entry_id"] for s in body["stopped_labor_entries"]}

        # Every entry is now stopped in DB + labor.stopped_by_hold audit
        # emitted per entry.
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, stopped_at FROM labor_entries "
                "WHERE operation_id = CAST(:o AS uuid)"
            ).bindparams(o=op_id)).fetchall()
            audit_rows = conn.execute(text(
                "SELECT entity_id FROM audit_records "
                "WHERE action_type = 'labor.stopped_by_hold' "
                "  AND entity_id = ANY(CAST(:ids AS uuid[]))"
            ).bindparams(ids=list(stopped_ids))).fetchall()
            # Pending stop-request transitioned to expired.
            req = conn.execute(text(
                "SELECT status FROM stop_requests "
                "WHERE id = CAST(:id AS uuid)"
            ).bindparams(id=pending_id)).fetchone()
        engine.dispose()

        for entry_id, stopped_at in rows:
            assert stopped_at is not None, (
                f"labor entry {entry_id} should have stopped_at set"
            )
        assert len(audit_rows) == 2, (
            f"expected 2 labor.stopped_by_hold audits, got {len(audit_rows)}"
        )
        assert req is not None
        assert req[0] == "expired", (
            f"pending stop-request should have expired, got {req[0]}"
        )

    # ------------------------------------------------------------------
    # Canonical route surface
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_canonical_labor_entries_patch_path(self) -> None:
        """PATCH /api/labor-entries/{id} is the canonical correction path.

        Mirrors the legacy /api/labor/{id} PATCH; both exist for now
        (legacy kept as compatibility alias).  This test pins the
        canonical URL so a future slice that removes the alias does
        not break contract coverage.
        """
        wo = await self._make_wo_and_release()
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id FROM operations WHERE work_order_id = CAST(:w AS uuid)"
            ).bindparams(w=wo["id"])).fetchone()
        engine.dispose()
        op_id = str(row[0])
        await self._start_op_as(op_id, OPERATOR_ONE_ID)
        labor = await self._find_active_labor_for(op_id, OPERATOR_ONE_ID)

        self._override_as(OPERATOR_ONE_ID, roles=["operator"])
        async with await self._client() as client:
            resp = await client.patch(
                f"/api/labor-entries/{labor['id']}",
                json={
                    "version": labor["version"],
                    "notes": "canonical path test",
                },
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        assert resp.json()["notes"] == "canonical path test"
