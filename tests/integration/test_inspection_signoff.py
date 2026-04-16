#!/usr/bin/env python3
"""
Module:   Test Inspection + Sign-Off + SoD
Purpose:  Integration tests for slice 2B done-means: real HTTP flow
          proving inspection recording, sign-off recording, hold-point
          gating, COMP-003 completeness, and SOD-001/SOD-005 enforcement.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Slice 2B done-means requires proving each gate through real route +
  service + database flow.  Service-only tests would let UI-only or
  route-only enforcement drift in; real HTTP calls are the contract.
  Each failure scenario is paired with an allowed-path scenario so the
  suite cannot pass by merely blocking everything.
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
RELEASER_USER_ID = "00000000-0000-0000-0000-000000000099"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-00000000009A"
SECOND_INSPECTOR_ID = "00000000-0000-0000-0000-00000000009B"


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
    """Idempotently seed an extra test user to satisfy FK constraints."""
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
                id=uid, sub=f"test-{uid[-4:]}", email=email, name=name,
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_test_program() -> str:
    engine = create_engine(_SYNC_URL)
    prog_id = str(uuid.uuid4())
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id FROM programs LIMIT 1")).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), 'TST-2B', 'Test Program 2B',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestInspectionSignOff:
    """Real HTTP integration for inspection, sign-off, hold gating, SoD."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_test_program()
        _ensure_user(RELEASER_USER_ID, "rel@test.com", "Releaser")
        _ensure_user(INSPECTOR_USER_ID, "insp@test.com", "Inspector")
        _ensure_user(SECOND_INSPECTOR_ID, "insp2@test.com", "Second Inspector")

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
                    "   OR action_type LIKE 'inspection.%' "
                    "   OR action_type LIKE 'sign_off.%'"
                ))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("UPDATE part_instances SET reason_ncr_id = NULL"))
                conn.execute(text("DELETE FROM nonconformance_reports"))
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

    # ------------------------------------------------------------------
    # Auth-override helpers
    # ------------------------------------------------------------------

    def _override_as(self, user_id: str, roles: list[str]) -> None:
        """Set request.state.auth to a fully-permissioned context for user_id.

        We grant union-of-all-permissions so route-level permission
        gates pass; the SoD/COMP gates we are testing live in the
        service layer, which is the actual subject under test.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        all_perms: set[str] = set()
        for perms in ROLE_PERMISSIONS.values():
            all_perms |= perms
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"override-{user_id[-4:]}",
            roles=roles, programs=["*"],
            permissions=frozenset(all_perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    async def _make_op(
        self, *, inspection_hold: bool = False, safety_critical: bool = False,
        sign_off_roles: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create WO + op, release WO with second user, start op as SYSTEM.

        Returns the started-op dict (so caller has the latest version).
        The starting user is the SYSTEM/local-dev user (default override
        cleared); call sites override auth as needed for SoD scenarios.
        """
        # Default override (no override) → SYSTEM_USER_ID per local auth
        self._clear_override()
        async with await self._client() as client:
            # Create WO
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": "E-2B-001",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            wo = resp.json()

            # Create op with optional hold / safety / required roles
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                json={
                    "description": "Op 1",
                    "sequence": 1,
                    "inspection_hold": inspection_hold,
                    "safety_critical": safety_critical,
                    "sign_off_roles_required": sign_off_roles or [],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            op = resp.json()

        # Release WO with a different user (SOD-004)
        self._override_as(RELEASER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                json={"version": wo["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 200, resp.text

        # Start op as the SYSTEM user (becomes labor)
        self._clear_override()
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
            assert resp.status_code == 200, resp.text
            return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Inspection — basic + SOD-001
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_inspection_recorded(self) -> None:
        """Done-means: inspections can be recorded."""
        op = await self._make_op(inspection_hold=True)

        # Inspector (different from SYSTEM operator) records inspection
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={
                    "criteria": "Surface finish ≥ Ra 1.6",
                    "result": "accept",
                    "measurements": {"ra_um": 1.2},
                    "evidence_refs": [],
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["result"] == "accept"
        assert body["execution_cycle"] == 1

    @pytest.mark.asyncio
    async def test_sod001_self_inspection_blocked(self) -> None:
        """SOD-001: operator (who has labor entry) cannot inspect own op."""
        op = await self._make_op(inspection_hold=True)

        # SYSTEM user has labor (from start_operation) → cannot inspect
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 422
        assert "SOD-001" in str(resp.json())

    @pytest.mark.asyncio
    async def test_sod001_no_labor_inspector_allowed(self) -> None:
        """SOD-001 allow-path: an inspector with no labor entries can inspect."""
        op = await self._make_op(inspection_hold=True)

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text

    # ------------------------------------------------------------------
    # Sign-off — basic + SOD-005
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_signoff_recorded(self) -> None:
        """Done-means: sign-offs can be recorded."""
        op = await self._make_op()

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["role"] == "inspector"
        assert body["execution_cycle"] == 1

    @pytest.mark.asyncio
    async def test_signoff_role_not_held_rejected(self) -> None:
        """GC-3 round 5: claiming a role the user does not hold is
        rejected with 422 SIGNOFF_ROLE_NOT_HELD before any insert.

        Prior to round 5 the backend trusted the client-claimed role
        verbatim, so an inspector could submit role="operator" and
        falsely advance the covered-role gate.  The check fires AFTER
        scope/state checks and BEFORE SOD-005 + insert + audit, so a
        forged role never produces a sign_off_event row OR an audit
        record (clean slate for the rejected attempt).
        """
        op = await self._make_op(
            safety_critical=False,
            sign_off_roles=["operator", "inspector"],
        )

        # User holds inspector ONLY but tries to sign as operator.
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "operator", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422, r.text
        body = r.json()
        # Stable code surface — the route-layer 422 envelope wraps
        # the service exception's detail dict in `detail`.
        detail = body.get("detail", body)
        assert detail.get("code") == "SIGNOFF_ROLE_NOT_HELD", body

    @pytest.mark.asyncio
    async def test_signoff_role_not_in_required_set_rejected(self) -> None:
        """GC-3 round 5: an op with non-empty sign_off_roles_required
        rejects a sign-off whose role is outside that set, even if the
        caller legitimately holds it.

        Reason: a held-but-not-required sign-off cannot advance
        completion (it isn't in COMP-003's required set), so accepting
        it would write an audit-log row that misleads future readers
        into thinking the gate moved.  The signoffs_completed_roles
        gate is also strictly stronger when only required roles can
        be recorded.
        """
        op = await self._make_op(
            safety_critical=False,
            sign_off_roles=["inspector"],
        )

        # User holds engineer + inspector; engineer is not required.
        self._override_as(
            INSPECTOR_USER_ID, roles=["engineer", "inspector"],
        )
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "engineer", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 422, r.text
        detail = r.json().get("detail", r.json())
        assert detail.get("code") == "SIGNOFF_ROLE_NOT_REQUIRED", r.text

    @pytest.mark.asyncio
    async def test_sod005_blocks_same_user_two_roles(self) -> None:
        """SOD-005: on safety-critical, one user cannot sign two
        DIFFERENT required roles even if they legitimately hold both.

        GC-3 round 5: caller now legitimately holds both roles (the
        round-5 role-authenticity gate would otherwise reject the
        first sign-off as SIGNOFF_ROLE_NOT_HELD).  This keeps the
        test honest — SOD-005 is the actual subject under test.
        """
        op = await self._make_op(
            safety_critical=True,
            sign_off_roles=["operator", "inspector"],
        )

        # Same user holds BOTH roles legitimately and signs both.
        self._override_as(
            INSPECTOR_USER_ID, roles=["operator", "inspector"],
        )
        async with await self._client() as client:
            r1 = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "operator", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r1.status_code == 201, r1.text

            r2 = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r2.status_code == 422
        assert "SOD-005" in str(r2.json())

    @pytest.mark.asyncio
    async def test_sod005_distinct_users_allowed(self) -> None:
        """SOD-005 allow-path: distinct users covering distinct
        required roles both succeed.

        GC-3 round 5: each caller legitimately holds the role they
        sign as — no forged-role assumption.
        """
        op = await self._make_op(
            safety_critical=True,
            sign_off_roles=["operator", "inspector"],
        )

        # User A holds operator and signs operator.
        self._override_as(INSPECTOR_USER_ID, roles=["operator"])
        async with await self._client() as client:
            r1 = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "operator", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r1.status_code == 201, r1.text

        # User B holds inspector and signs inspector.
        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r2 = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r2.status_code == 201, r2.text

    # ------------------------------------------------------------------
    # Hold-point gating (COMP-001) and sign-off completeness (COMP-003)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_inspection_hold_blocks_completion(self) -> None:
        """COMP-001: hold without accepted inspection blocks completion."""
        op = await self._make_op(inspection_hold=True)

        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 422
        assert "COMP-001" in str(resp.json())

    @pytest.mark.asyncio
    async def test_inspection_hold_satisfied_allows_completion(self) -> None:
        """COMP-001 allow-path: accepted inspection in current cycle unblocks."""
        op = await self._make_op(inspection_hold=True)

        # Inspector accepts
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

        # Operator completes
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "complete"

    @pytest.mark.asyncio
    async def test_signoff_required_blocks_completion(self) -> None:
        """COMP-003: missing required sign-off role blocks completion."""
        op = await self._make_op(sign_off_roles=["inspector"])

        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 422
        assert "COMP-003" in str(resp.json())

    @pytest.mark.asyncio
    async def test_signoff_satisfied_allows_completion(self) -> None:
        """COMP-003 allow-path: required role signed → completion succeeds."""
        op = await self._make_op(sign_off_roles=["inspector"])

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert resp.status_code == 200, resp.text

    # The prior-cycle inspection-invalidation invariant (COMP-001
    # over current execution_cycle) is exercised end-to-end via the
    # GC-2 NCR-driven reopen path in
    # tests/integration/test_reopen_reactivate.py::
    # test_prior_cycle_inspection_does_not_satisfy_after_reopen.
    # The original test here went through the /rework surface which
    # is retired (build spec v1.4.2 GC-2 removes the transient
    # rework state).

    # ------------------------------------------------------------------
    # Full happy path on a safety-critical operation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_full_2b_happy_path_safety_critical(self) -> None:
        """End-to-end: hold + safety-critical + dual sign-off → complete OK."""
        op = await self._make_op(
            inspection_hold=True,
            safety_critical=True,
            sign_off_roles=["operator", "inspector"],
        )

        # GC-3 round 5: caller legitimately holds the role they sign
        # as.  Inspector here also holds operator so the operator
        # sign-off is honest under the new role-authenticity gate.
        # (Inspector does the inspection separately so SOD-001 still
        # uses a no-labor user.)
        self._override_as(
            INSPECTOR_USER_ID, roles=["operator", "inspector"],
        )
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                json={"criteria": "x", "result": "accept"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "operator", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

        # Distinct second inspector signs the inspector role (SOD-005)
        self._override_as(SECOND_INSPECTOR_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/sign-off",
                json={"role": "inspector", "action": "approve"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 201, r.text

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/complete",
                json={"version": op["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "complete"
