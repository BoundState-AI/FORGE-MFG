#!/usr/bin/env python3
"""
Module:   Test NCR Workflow
Purpose:  Integration tests for slice 3A done-means: real HTTP flow
          proving NCR create, disposition, close, audit, version checks,
          program scope, and SOD-002 enforcement.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  Slice 3A done-means requires proving the full NCR lifecycle through
  real route + service + database flow.  Service-only tests would let
  route- or middleware-layer regressions slip in.  Each failure path
  is paired with an allow-path test so the suite cannot pass by
  blocking everything.
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
DISPOSITIONER_USER_ID = "00000000-0000-0000-0000-0000000000A1"
CLOSER_USER_ID = "00000000-0000-0000-0000-0000000000A2"


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
    """Idempotently seed a test user to satisfy FK constraints."""
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
                VALUES (CAST(:id AS uuid), 'TST-3A', 'Test Program 3A',
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(id=prog_id, sys=SYSTEM_USER_ID))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestNCRWorkflow:
    """Real HTTP integration for the full NCR lifecycle (slice 3A)."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_test_program()
        _ensure_user(DISPOSITIONER_USER_ID, "disp@test.com", "Dispositioner")
        _ensure_user(CLOSER_USER_ID, "close@test.com", "Closer")

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
                    "   OR action_type LIKE 'sign_off.%' "
                    "   OR action_type LIKE 'ncr.%'"
                ))
                conn.execute(text("DELETE FROM nonconformance_reports"))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text("DELETE FROM inspection_events"))
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

    # ------------------------------------------------------------------
    # Auth-override helpers
    # ------------------------------------------------------------------

    def _override_as(
        self, user_id: str, roles: list[str],
        permissions: set[str] | None = None,
        programs: list[str] | None = None,
    ) -> None:
        """Set request.state.auth to a custom AuthContext.

        When `permissions` is None, the override grants the union of all
        role permissions so the SoD/transition gates we are exercising
        are the actual subject under test.  When `permissions` is given,
        only those are granted — used by the explicit permission test
        to prove route-level authz blocks correctly.
        """
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        if permissions is None:
            perms: set[str] = set()
            for p in ROLE_PERMISSIONS.values():
                perms |= p
        else:
            perms = permissions

        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"override-{user_id[-4:]}",
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
    # Build helpers
    # ------------------------------------------------------------------

    async def _make_wo(self) -> dict[str, Any]:
        """Create a WO using the SYSTEM user (default local auth)."""
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                "/api/work-orders",
                json={
                    "program_id": self.prog_id,
                    "engine_serial": "E-3A-001",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            return resp.json()  # type: ignore[no-any-return]

    async def _create_ncr(
        self, wo_id: str, *, mrb_required: bool = False,
    ) -> dict[str, Any]:
        """Create an NCR as the SYSTEM user (the initiator for SOD-002)."""
        self._clear_override()
        async with await self._client() as client:
            resp = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo_id,
                    "description": "Surface finish out of tolerance",
                    "mrb_required": mrb_required,
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert resp.status_code == 201, resp.text
            return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_ncr(self) -> None:
        """Done-means: NCRs can be created and start in `open`."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])
        assert ncr["status"] == "open"
        assert ncr["disposition"] == "pending"
        assert ncr["ncr_number"].startswith("NCR-")
        assert ncr["initiated_by_id"] == SYSTEM_USER_ID

    @pytest.mark.asyncio
    async def test_cross_program_create_denied(self) -> None:
        """Program scope: cannot create an NCR against a WO in another program.

        UX spec v1.5.1 §3.2 / §6.7.4: cross-program access returns 404
        (not 403) to prevent existence leaks across programs.
        """
        wo = await self._make_wo()

        # Switch to a user with a different program
        self._override_as(
            DISPOSITIONER_USER_ID, roles=["engineer"], programs=["OTHER-PROG"],
        )
        async with await self._client() as client:
            resp = await client.post(
                "/api/ncrs",
                json={
                    "work_order_id": wo["id"],
                    "description": "should fail scope",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_ncr_detail(self) -> None:
        wo = await self._make_wo()
        created = await self._create_ncr(wo["id"])

        async with await self._client() as client:
            resp = await client.get(f"/api/ncrs/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    @pytest.mark.asyncio
    async def test_list_ncrs_paginated(self) -> None:
        wo = await self._make_wo()
        await self._create_ncr(wo["id"])
        await self._create_ncr(wo["id"])

        async with await self._client() as client:
            resp = await client.get("/api/ncrs?page=1&page_size=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2
        assert len(body["items"]) == 1
        assert body["page_size"] == 1

    @pytest.mark.asyncio
    async def test_cross_program_get_denied(self) -> None:
        """Cross-program detail GET returns 404 per UX spec §3.2 / §6.7.4."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        self._override_as(
            DISPOSITIONER_USER_ID, roles=["engineer"], programs=["OTHER-PROG"],
        )
        async with await self._client() as client:
            resp = await client.get(f"/api/ncrs/{ncr['id']}")
        self._clear_override()
        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # Disposition + SOD-002
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_disposition_ncr(self) -> None:
        """Done-means: a non-initiator can disposition an NCR."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        # Different user (not the SYSTEM initiator) dispositions
        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "rework",
                    "disposition_rationale": "Recut to spec; verify Ra after.",
                },
            )
        self._clear_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "dispositioned"
        assert body["disposition"] == "rework"
        assert body["disposition_rationale"].startswith("Recut")

    @pytest.mark.asyncio
    async def test_sod002_initiator_blocked(self) -> None:
        """SOD-002: the NCR initiator cannot disposition the same NCR."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        # SYSTEM user is the initiator (per _create_ncr); attempt to
        # disposition with the same identity → must be blocked.
        async with await self._client() as client:
            resp = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "use_as_is",
                    "disposition_rationale": "self-clear",
                },
            )
        assert resp.status_code == 422
        assert "SOD-002" in str(resp.json())

    @pytest.mark.asyncio
    async def test_disposition_pending_rejected(self) -> None:
        """Disposition decision cannot be `pending` — would leave NCR undecided."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "pending",
                    "disposition_rationale": "still thinking",
                },
            )
        self._clear_override()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_disposition_version_conflict_409(self) -> None:
        """Optimistic locking: stale version returns 409."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            resp = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": 999,
                    "disposition": "scrap",
                    "disposition_rationale": "x",
                },
            )
        self._clear_override()
        assert resp.status_code == 409

    # ------------------------------------------------------------------
    # Close + transition guards
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_close_requires_dispositioned(self) -> None:
        """Closing an `open` NCR is rejected by the transition map."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        self._override_as(CLOSER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            resp = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                json={"version": ncr["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_full_workflow_create_disposition_close(self) -> None:
        """Done-means: full create → disposition → close path succeeds."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        # Disposition by a different user (SOD-002)
        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "repair",
                    "disposition_rationale": "Local weld repair per WPS-117",
                },
            )
            assert r.status_code == 200, r.text
            disp = r.json()

        # Close by qa_manager
        self._override_as(CLOSER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{disp['id']}/close",
                json={
                    "version": disp["version"],
                    "root_cause": "Tooling drift on station 4",
                },
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        closed = r.json()
        assert closed["status"] == "closed"
        assert closed["root_cause"].startswith("Tooling drift")

    @pytest.mark.asyncio
    async def test_close_requires_qa_manager_permission(self) -> None:
        """Permission gate: a user without ncr:close gets 403."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        # Disposition first so we are testing the close permission, not
        # the transition map.
        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "scrap",
                    "disposition_rationale": "irrecoverable",
                },
            )
            assert r.status_code == 200, r.text
            disp = r.json()

        # Engineer role does NOT have ncr:close — only qa_manager does.
        # Pass only the engineer permissions explicitly so route gate fires.
        from forge.api.auth import ROLE_PERMISSIONS
        engineer_perms = set(ROLE_PERMISSIONS["engineer"])
        assert "ncr:close" not in engineer_perms
        self._override_as(
            DISPOSITIONER_USER_ID, roles=["engineer"],
            permissions=engineer_perms,
        )
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{disp['id']}/close",
                json={"version": disp["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        self._clear_override()
        assert r.status_code == 403

    # ------------------------------------------------------------------
    # Audit trail
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_audit_records_for_ncr_lifecycle(self) -> None:
        """Audit chain captures create, disposition, close events."""
        wo = await self._make_wo()
        ncr = await self._create_ncr(wo["id"])

        self._override_as(DISPOSITIONER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                json={
                    "version": ncr["version"],
                    "disposition": "use_as_is",
                    "disposition_rationale": "Within MRB tolerance",
                },
            )
            assert r.status_code == 200, r.text
            disp = r.json()

        self._override_as(CLOSER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{disp['id']}/close",
                json={"version": disp["version"]},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            assert r.status_code == 200, r.text
        self._clear_override()

        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT action_type FROM audit_records
                WHERE entity_id = CAST(:id AS uuid)
                  AND action_type LIKE 'ncr.%'
                ORDER BY sequence_number
            """).bindparams(id=ncr["id"])).fetchall()
        engine.dispose()
        actions = [r[0] for r in rows]
        assert actions == ["ncr.create", "ncr.disposition", "ncr.close"]
